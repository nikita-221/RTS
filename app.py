from flask import Flask, render_template, request, redirect, session, flash
import sqlite3
import os
import re
from datetime import datetime
import pdfplumber
from docx import Document
from PIL import Image
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import json

app = Flask(__name__)
app.secret_key = "resume_secret"

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"pdf", "docx", "png", "jpg", "jpeg"}

PERMANENT_ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "nikitabharmota@gmail.com")
PERMANENT_ADMIN_NAME = os.getenv("ADMIN_NAME", "Admin")
PERMANENT_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "nikita890")

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "are", "was",
    "will", "can", "your", "you", "have", "has", "had", "but", "not",
    "job", "role", "skills", "experience", "work", "team", "able", "using",
}

# ---------------- DATABASE ----------------
def get_db():
    conn = sqlite3.connect("database.db", timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def create_tables():
    conn = get_db()
    cur = conn.cursor()

    # Enable WAL mode (prevents database lock)
    cur.execute("PRAGMA journal_mode=WAL")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT UNIQUE,
        password TEXT,
        role TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS resumes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        filename TEXT,
        score INTEGER,
        missing_skills TEXT,
        summary TEXT,
        top_keywords TEXT,
        uploaded_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS admin_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        status TEXT DEFAULT 'pending',
        requested_at TEXT
    )
    """)

    conn.commit()
    conn.close()


def ensure_default_admin():
    conn = get_db()
    cur = conn.cursor()
    
    # Only create default admin if NO admins exist at all
    existing_admin = cur.execute(
        "SELECT id FROM users WHERE role = 'admin'"
    ).fetchone()
    
    if not existing_admin:
        admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
        admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
        admin_name = os.getenv("ADMIN_NAME", "Admin")
        
        cur.execute(
            "INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)",
            (
                admin_name,
                admin_email,
                generate_password_hash(admin_password),
                "admin"
            )
        )
        conn.commit()
    conn.close()

def migrate_database():
    conn = get_db()
    cur = conn.cursor()
    migrations = [
        ("missing_skills", "TEXT"),
        ("summary", "TEXT"),
        ("top_keywords", "TEXT"),
        ("uploaded_at", "TEXT"),
        ("ai_tips", "TEXT"),
    ]
    for column, column_type in migrations:
        try:
            cur.execute(f"ALTER TABLE resumes ADD COLUMN {column} {column_type}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()

create_tables()
migrate_database()  # Automatically keep the resume table schema updated for new fields

# ---------------- TEXT EXTRACTION ----------------
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text(file_path):

    if file_path.lower().endswith(".pdf"):
        text = ""
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""
        return text

    elif file_path.lower().endswith(".docx"):
        doc = Document(file_path)
        return " ".join([p.text for p in doc.paragraphs])

    elif file_path.lower().endswith((".png", ".jpg", ".jpeg")):
        try:
            import pytesseract
            image = Image.open(file_path)
            return pytesseract.image_to_string(image)
        except Exception:
            return "Image uploaded (OCR not available). Please install pytesseract for image parsing."

    return ""

# ---------------- RESUME ANALYZER ----------------
def analyze_resume(resume_text, jd):

    resume_words = set(re.findall(r"\b[a-zA-Z]{2,}\b", resume_text.lower()))
    jd_words = set(re.findall(r"\b[a-zA-Z]{2,}\b", jd.lower()))

    matched = resume_words & jd_words

    score = int((len(matched) / len(jd_words)) * 100) if jd_words else 0

    missing = jd_words - resume_words

    return score, list(missing)[:10]

def get_top_keywords(text, limit=6):
    words = re.findall(r"\b[a-zA-Z]{2,}\b", text.lower())
    filtered = [w for w in words if w not in STOPWORDS]
    freq = {}
    for word in filtered:
        freq[word] = freq.get(word, 0) + 1

    keywords = sorted(freq.keys(), key=lambda w: (-freq[w], w))
    return keywords[:limit]

def generate_ai_tips(score, missing, jd_keywords):
    tips = []

    if score >= 80:
        tips.append("🎉 Excellent match! Your resume aligns strongly with the job description. Focus on tailoring your cover letter to highlight specific achievements.")
    elif score >= 50:
        tips.append("👍 Good foundation. Enhance your resume by incorporating more industry-specific keywords and quantifying your accomplishments.")
    else:
        tips.append("🔧 Room for improvement. Revise your resume to include key terms from the job description and emphasize relevant skills and experiences.")

    if missing:
        tips.append(f"📝 Add these missing keywords: {', '.join(missing[:6])}. Integrate them naturally into your experience and skills sections.")

    if jd_keywords:
        tips.append(f"🔍 Prioritize these top job keywords: {', '.join(jd_keywords[:6])}. Use them strategically in your resume summary and bullet points.")

    tips.append("💡 Use action verbs like 'Led', 'Developed', 'Optimized' to start bullet points and demonstrate impact.")
    tips.append("📊 Quantify achievements with metrics (e.g., 'Increased efficiency by 30%') to make your contributions tangible.")
    tips.append("🎯 Customize your resume for each application by mirroring the job description's language and requirements.")

    return tips

def generate_advanced_report(resume_text, jd, score, missing, jd_keywords):
    report = {
        "overall_score": score,
        "strengths": [],
        "weaknesses": [],
        "recommendations": [],
        "keyword_analysis": {
            "matched": len(set(re.findall(r"\b[a-zA-Z]{2,}\b", resume_text.lower())) & set(re.findall(r"\b[a-zA-Z]{2,}\b", jd.lower()))),
            "total_jd": len(set(re.findall(r"\b[a-zA-Z]{2,}\b", jd.lower()))),
            "top_keywords": jd_keywords[:10]
        }
    }

    if score >= 80:
        report["strengths"].append("Strong keyword alignment with job requirements")
        report["strengths"].append("Comprehensive coverage of essential skills")
    elif score >= 50:
        report["strengths"].append("Decent match with room for optimization")
        report["weaknesses"].append("Missing some key terms and phrases")
    else:
        report["weaknesses"].append("Significant gaps in keyword matching")
        report["weaknesses"].append("Limited alignment with job description")

    report["recommendations"] = generate_ai_tips(score, missing, jd_keywords)

    return report

def build_resume_summary(score, missing):
    if score >= 80:
        return "Strong alignment: your resume contains most of the key terms from the job description."
    if score >= 50:
        return "Moderate alignment: some keywords are present, but you should emphasize missing skills and technical strengths."
    return "Low alignment: your resume would benefit from more direct matches to the job description keywords and stronger impact statements."


# ---------------- ROUTES ----------------
@app.route("/")
def index():
    return render_template("home.html")

# REGISTER
@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        account_type = request.form.get("account_type", "user")

        if not name or not email or not password:
            flash("All fields are required.", "danger")
            return render_template("register.html", selected_account=account_type, name=name, email=email)

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("register.html", selected_account=account_type, name=name, email=email)

        hashed_password = generate_password_hash(password)

        conn = get_db()
        cur = conn.cursor()

        try:
            role = "user"
            
            # Check if registering as admin
            if account_type == "admin":
                # Check if any admin exists
                conn_check = get_db()
                cur_check = conn_check.cursor()
                existing_admin = cur_check.execute(
                    "SELECT id FROM users WHERE role = 'admin'"
                ).fetchone()
                conn_check.close()
                
                # If no admin exists, auto-approve this one as admin
                if not existing_admin:
                    role = "admin"
            
            cur.execute(
                "INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)",
                (name, email, hashed_password, role)
            )
            user_id = cur.lastrowid

            # If still requesting admin (and not auto-approved as first admin)
            if account_type == "admin" and role == "user":
                cur.execute(
                    "INSERT INTO admin_requests(user_id, requested_at) VALUES(?, ?)",
                    (user_id, datetime.now().isoformat())
                )

            conn.commit()

        except sqlite3.IntegrityError:
            conn.close()
            flash("Email already registered!", "danger")
            return render_template("register.html", selected_account=account_type, name=name, email=email)

        conn.close()

        if account_type == "admin" and role == "admin":
            flash("Admin account created! You can now login as admin.", "success")
        elif account_type == "admin":
            flash("Admin request sent to the main admin. Please wait for approval.", "success")
        else:
            flash("Account created successfully. Please log in.", "success")

        return redirect("/login")

    return render_template("register.html")

# LOGIN
@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form["email"]
        password = request.form["password"]
        login_type = request.form.get("login_type", "user")

        conn = get_db()
        cur = conn.cursor()

        user = cur.execute(
            "SELECT * FROM users WHERE email=?",
            (email,)
        ).fetchone()

        conn.close()

        if user and check_password_hash(user["password"], password):

            session["user_id"] = user["id"]
            session["user_email"] = user["email"]
            session["role"] = user["role"]

            if login_type == "admin":
                if user["role"] == "admin":
                    return redirect("/admin")
                else:
                    flash("Access denied: You are not an admin.", "danger")
                    return render_template("login.html", email=email, login_type=login_type)
            else:
                return redirect("/dashboard")

        flash("Invalid email or password.", "danger")
        return render_template("login.html", email=email, login_type=login_type)

    return render_template("login.html")

# DASHBOARD
@app.route("/dashboard")
def dashboard():

    if "user_id" not in session:
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()

    user = cur.execute(
        "SELECT name, role FROM users WHERE id = ?",
        (session["user_id"],)
    ).fetchone()

    history = cur.execute(
        "SELECT resumes.filename, resumes.score, resumes.missing_skills, resumes.summary, resumes.top_keywords, resumes.uploaded_at "
        "FROM resumes "
        "JOIN users ON resumes.user_id = users.id "
        "WHERE users.email = ? "
        "ORDER BY resumes.uploaded_at DESC",
        (session["user_email"],)
    ).fetchall()

    admin_request = cur.execute(
        "SELECT status FROM admin_requests WHERE user_id = ? ORDER BY requested_at DESC LIMIT 1",
        (session["user_id"],)
    ).fetchone()

    conn.close()

    admin_request_status = admin_request["status"] if admin_request else None

    return render_template(
        "dashboard.html",
        user=user,
        history=history,
        admin_request_status=admin_request_status
    )

# HISTORY
@app.route("/history")
def history():

    if "user_id" not in session:
        return redirect("/login")

    sort_by = request.args.get("sort", "uploaded_at_desc")
    order_by = {
        "uploaded_at_desc": "resumes.uploaded_at DESC",
        "uploaded_at_asc": "resumes.uploaded_at ASC",
        "score_desc": "resumes.score DESC",
        "score_asc": "resumes.score ASC"
    }.get(sort_by, "resumes.uploaded_at DESC")

    conn = get_db()
    cur = conn.cursor()

    history = cur.execute(
        "SELECT resumes.id, resumes.filename, resumes.score, resumes.summary, resumes.top_keywords, resumes.uploaded_at "
        "FROM resumes "
        "JOIN users ON resumes.user_id = users.id "
        "WHERE users.email = ? "
        f"ORDER BY {order_by}",
        (session["user_email"],)
    ).fetchall()

    conn.close()

    is_admin = session.get("role") == "admin"
    return render_template("history.html", history=history, is_admin=is_admin, sort_by=sort_by)

# DELETE RESUME
@app.route("/delete_resume/<int:resume_id>")
def delete_resume(resume_id):

    if "role" not in session or session["role"] != "admin":
        return "Unauthorized access", 403

    conn = get_db()
    cur = conn.cursor()

    resume = cur.execute(
        "SELECT filename FROM resumes WHERE id = ?",
        (resume_id,)
    ).fetchone()

    if not resume:
        conn.close()
        return "Resume not found", 404

    resume_path = os.path.join(UPLOAD_FOLDER, resume["filename"])
    if os.path.exists(resume_path):
        try:
            os.remove(resume_path)
        except OSError:
            pass

    cur.execute("DELETE FROM resumes WHERE id = ?", (resume_id,))
    conn.commit()
    conn.close()

    return redirect("/history")

# DELETE USER
@app.route("/delete_user/<int:user_id>")
def delete_user(user_id):

    if "role" not in session or session["role"] != "admin":
        return "Unauthorized access", 403

    conn = get_db()
    cur = conn.cursor()

    target_user = cur.execute(
        "SELECT email FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    if not target_user:
        conn.close()
        return "User not found", 404

    if target_user["email"] == PERMANENT_ADMIN_EMAIL:
        conn.close()
        return "Cannot delete the permanent admin.", 403

    if session.get("user_id") == user_id:
        conn.close()
        return "Cannot delete your own admin account.", 400

    resumes = cur.execute(
        "SELECT filename FROM resumes WHERE user_id = ?",
        (user_id,)
    ).fetchall()

    for resume in resumes:
        resume_path = os.path.join(UPLOAD_FOLDER, resume["filename"])
        if os.path.exists(resume_path):
            try:
                os.remove(resume_path)
            except OSError:
                pass

    cur.execute("DELETE FROM resumes WHERE user_id = ?", (user_id,))
    cur.execute("DELETE FROM admin_requests WHERE user_id = ?", (user_id,))
    cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()

    return redirect("/admin")

# UPLOAD RESUME
@app.route("/upload", methods=["GET", "POST"])
def upload():

    if "user_id" not in session:
        return redirect("/login")

    if request.method == "POST":

        file = request.files.get("resume")
        jd = request.form.get("jd", "").strip()

        if not file or file.filename == "" or not allowed_file(file.filename):
            flash("Please upload a valid resume file (PDF, DOCX, PNG, JPG).", "danger")
            return render_template("upload.html")

        if not jd:
            flash("Job description is required.", "danger")
            return render_template("upload.html")

        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        saved_filename = f"{session['user_id']}_{timestamp}_{filename}"
        path = os.path.join(UPLOAD_FOLDER, saved_filename)
        file.save(path)

        text = extract_text(path)

        score, missing = analyze_resume(text, jd)
        top_jd_keywords = get_top_keywords(jd)
        ai_tips = generate_ai_tips(score, missing, top_jd_keywords)
        summary = build_resume_summary(score, missing)
        advanced_report = generate_advanced_report(text, jd, score, missing, top_jd_keywords)

        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            "INSERT INTO resumes(user_id,filename,score,missing_skills,summary,top_keywords,ai_tips,uploaded_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                session["user_id"],
                saved_filename,
                score,
                ", ".join(missing),
                summary,
                ", ".join(top_jd_keywords),
                json.dumps(ai_tips),
                datetime.now().isoformat(),
            )
        )

        conn.commit()

        resume_id = cur.lastrowid

        conn.close()

        return render_template(
            "result.html",
            filename=saved_filename,
            score=score,
            missing_skills=missing,
            ai_tips=ai_tips,
            summary=summary,
            top_keywords=top_jd_keywords,
            advanced_report=advanced_report,
            resume_id=resume_id
        )

    return render_template("upload.html")

# REQUEST ADMIN ACCESS
@app.route("/request_admin", methods=["POST"])
def request_admin():

    if "user_id" not in session:
        return redirect("/login")

    if session.get("role") == "admin":
        flash("You already have admin access.", "info")
        return redirect("/dashboard")

    conn = get_db()
    cur = conn.cursor()

    existing = cur.execute(
        "SELECT status FROM admin_requests WHERE user_id = ? ORDER BY requested_at DESC LIMIT 1",
        (session["user_id"],)
    ).fetchone()

    if not existing or existing["status"] != "pending":
        cur.execute(
            "INSERT INTO admin_requests(user_id, requested_at) VALUES(?, ?)",
            (session["user_id"], datetime.now().isoformat())
        )
        conn.commit()
        flash("Admin access request sent. Please wait for approval.", "success")
    else:
        flash("Your admin access request is already pending.", "info")

    conn.close()
    return redirect("/dashboard")

# ADMIN PANEL
@app.route("/admin")
def admin():

    if "role" not in session or session["role"] != "admin":
        return redirect("/login")

    sort_by = request.args.get("sort", "email")

    order_by = {
        "name": "users.name",
        "email": "users.email",
        "upload_count": "upload_count DESC",
        "highest_accuracy": "MAX(resumes.score) DESC",
        "average_accuracy": "AVG(resumes.score) DESC"
    }.get(sort_by, "users.email")

    conn = get_db()
    cur = conn.cursor()

    resumes = cur.execute(f"""
    SELECT users.id, users.name, users.email, users.role,
           COUNT(resumes.id) as upload_count,
           MAX(resumes.score) as max_score,
           AVG(resumes.score) as avg_score,
           GROUP_CONCAT(resumes.filename || ' (' || resumes.score || '%)', '; ') as resume_details
    FROM users
    LEFT JOIN resumes ON users.id = resumes.user_id
    GROUP BY users.id, users.name, users.email, users.role
    ORDER BY {order_by}
    """).fetchall()

    pending_requests = cur.execute(
        "SELECT admin_requests.id AS request_id, users.id AS user_id, users.name, users.email, admin_requests.requested_at "
        "FROM admin_requests "
        "JOIN users ON admin_requests.user_id = users.id "
        "WHERE admin_requests.status = 'pending' "
        "ORDER BY admin_requests.requested_at DESC"
    ).fetchall()

    conn.close()

    return render_template("admin.html", resumes=resumes, pending_requests=pending_requests, sort_by=sort_by, current_user_id=session.get("user_id"))

# LOGOUT
@app.route("/logout")
def logout():

    session.clear()

    return redirect("/")

#APPROVE ADMIN REQUEST
@app.route("/approve_admin_request/<int:request_id>")
def approve_admin_request(request_id):

    if "role" not in session or session["role"] != "admin":
        return redirect("/login")

    if session.get("user_email") != PERMANENT_ADMIN_EMAIL:
        flash("Only the permanent admin can approve admin access.", "danger")
        return redirect("/admin")

    conn = get_db()
    cur = conn.cursor()

    admin_request = cur.execute(
        "SELECT user_id FROM admin_requests WHERE id = ? AND status = 'pending'",
        (request_id,)
    ).fetchone()

    if admin_request:
        cur.execute(
            "UPDATE users SET role = 'admin' WHERE id = ?",
            (admin_request["user_id"],)
        )
        cur.execute(
            "UPDATE admin_requests SET status = 'approved' WHERE id = ?",
            (request_id,)
        )
        conn.commit()

    conn.close()
    return redirect("/admin")

# REJECT ADMIN REQUEST
@app.route("/reject_admin_request/<int:request_id>")
def reject_admin_request(request_id):

    if "role" not in session or session["role"] != "admin":
        return redirect("/login")

    if session.get("user_email") != PERMANENT_ADMIN_EMAIL:
        flash("Only the permanent admin can reject admin access.", "danger")
        return redirect("/admin")

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "UPDATE admin_requests SET status = 'rejected' WHERE id = ? AND status = 'pending'",
        (request_id,)
    )
    conn.commit()
    conn.close()

    return redirect("/admin")

#RECESULT DETAILS
@app.route("/recommendations/<int:resume_id>")
def recommendations(resume_id):

    if "user_id" not in session:
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()

    resume = cur.execute(
        "SELECT ai_tips FROM resumes WHERE id = ? AND user_id = ?",
        (resume_id, session["user_id"])
    ).fetchone()

    conn.close()

    if not resume:
        return "Resume not found or access denied."

    ai_tips = json.loads(resume["ai_tips"])

    return render_template("recommendations.html", ai_tips=ai_tips)

# RESULT DETAILS
@app.route("/result/<int:resume_id>")
def result(resume_id):

    if "user_id" not in session:
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()

    resume = cur.execute(
        "SELECT * FROM resumes WHERE id = ? AND user_id = ?",
        (resume_id, session["user_id"])
    ).fetchone()

    conn.close()

    if not resume:
        return "Resume not found or access denied."

    # Parse data
    missing_skills = resume["missing_skills"].split(", ") if resume["missing_skills"] else []
    top_keywords = resume["top_keywords"].split(", ") if resume["top_keywords"] else []
    ai_tips = json.loads(resume["ai_tips"]) if resume["ai_tips"] else []
    advanced_report = generate_advanced_report("", "", resume["score"], missing_skills, top_keywords)  # Simplified, as we don't have full text

    return render_template(
        "result.html",
        filename=resume["filename"],
        score=resume["score"],
        missing_skills=missing_skills,
        ai_tips=ai_tips,
        summary=resume["summary"],
        top_keywords=top_keywords,
        advanced_report=advanced_report,
        resume_id=resume_id
    )

# UPDATE USER ROLE (ADMIN ONLY)
@app.route("/update_role/<int:user_id>/<new_role>")
def update_role(user_id, new_role):

    if "role" not in session or session["role"] != "admin":
        return redirect("/login")

    if session.get("user_email") != PERMANENT_ADMIN_EMAIL:
        flash("Only the permanent admin can change user roles.", "danger")
        return redirect("/admin")

    if new_role not in ["user", "admin"]:
        return "Invalid role"

    conn = get_db()
    cur = conn.cursor()

    target_user = cur.execute(
        "SELECT email FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    if not target_user:
        conn.close()
        return "User not found", 404

    if target_user["email"] == PERMANENT_ADMIN_EMAIL and new_role != "admin":
        conn.close()
        return "Cannot remove the permanent admin.", 403

    cur.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
    conn.commit()
    conn.close()

    return redirect("/admin")

# ---------------- RUN ----------------

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)
