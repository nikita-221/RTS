from flask import Flask, render_template, request, redirect, session
import sqlite3
import os
import pdfplumber
from docx import Document
from PIL import Image
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "resume_secret"

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

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
        score INTEGER
    )
    """)

    conn.commit()
    conn.close()


create_tables()

# ---------------- TEXT EXTRACTION ----------------

def extract_text(file_path):

    if file_path.endswith(".pdf"):
        text = ""
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""
        return text

    elif file_path.endswith(".docx"):
        doc = Document(file_path)
        return " ".join([p.text for p in doc.paragraphs])

    elif file_path.endswith(".png") or file_path.endswith(".jpg"):
        return "Image uploaded (OCR not enabled)"

    return ""


# ---------------- RESUME ANALYZER ----------------

def analyze_resume(resume_text, jd):

    resume_words = set(resume_text.lower().split())
    jd_words = set(jd.lower().split())

    matched = resume_words & jd_words

    score = int((len(matched) / len(jd_words)) * 100) if jd_words else 0

    missing = jd_words - resume_words

    return score, list(missing)[:10]


# ---------------- ROUTES ----------------

@app.route("/")
def index():
    return render_template("index.html")


# REGISTER
@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form["name"]
        email = request.form["email"]
        password = generate_password_hash(request.form["password"])

        conn = get_db()
        cur = conn.cursor()

        try:
            cur.execute(
                "INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)",
                (name, email, password, "user")
            )
            conn.commit()

        except sqlite3.IntegrityError:
            conn.close()
            return "Email already registered!"

        conn.close()

        return redirect("/login")

    return render_template("register.html")


# LOGIN
@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form["email"]
        password = request.form["password"]

        conn = get_db()
        cur = conn.cursor()

        user = cur.execute(
            "SELECT * FROM users WHERE email=?",
            (email,)
        ).fetchone()

        conn.close()

        if user and check_password_hash(user["password"], password):

            session["user_id"] = user["id"]
            session["role"] = user["role"]

            if user["role"] == "admin":
                return redirect("/admin")

            return redirect("/dashboard")

        return "Invalid Email or Password"

    return render_template("login.html")


# DASHBOARD
@app.route("/dashboard")
def dashboard():

    if "user_id" not in session:
        return redirect("/login")

    return render_template("dashboard.html")


# UPLOAD RESUME
@app.route("/upload", methods=["GET", "POST"])
def upload():

    if "user_id" not in session:
        return redirect("/login")

    if request.method == "POST":

        file = request.files["resume"]
        jd = request.form["jd"]

        path = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(path)

        text = extract_text(path)

        score, missing = analyze_resume(text, jd)

        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            "INSERT INTO resumes(user_id,filename,score) VALUES(?,?,?)",
            (session["user_id"], file.filename, score)
        )

        conn.commit()
        conn.close()

        return render_template(
            "result.html",
            filename=file.filename,
            score=score,
            missing_skills=missing
        )

    return render_template("upload.html")


# ADMIN PANEL
@app.route("/admin")
def admin():

    if "role" not in session or session["role"] != "admin":
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()

    resumes = cur.execute("""
    SELECT users.name, resumes.filename, resumes.score
    FROM resumes
    JOIN users ON resumes.user_id = users.id
    """).fetchall()

    conn.close()

    return render_template("admin.html", resumes=resumes)


# LOGOUT
@app.route("/logout")
def logout():

    session.clear()

    return redirect("/")


# ---------------- RUN ----------------

if __name__ == "__main__":
    app.run(debug=True)