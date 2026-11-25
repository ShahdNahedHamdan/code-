
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from openai import OpenAI
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker, Session, declarative_base
import os
import shutil
import subprocess
import tempfile
import uuid
import re
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    ForeignKey,
    UniqueConstraint,
    event,
    Boolean,
    Text,
    DateTime,
    Float,
)

from datetime import datetime, timedelta
import hashlib
import hmac
import secrets
import jwt
import firebase_admin
from firebase_admin import credentials, auth as firebase_auth
import json
firebase_bearer = HTTPBearer(auto_error=False)
# =================================
# 1. Database configuration
# =================================

BASE_DIR = Path(__file__).resolve().parent
DATABASE_DIR = BASE_DIR / "data"
DATABASE_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_FILE = DATABASE_DIR / "codequest.db"
DATABASE_URL = f"sqlite:///{DATABASE_FILE.as_posix()}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)


@event.listens_for(engine, "connect")
def enable_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
# =================================
# User authentication model
# =================================

class UserModel(Base):
    __tablename__ = "users"

    id = Column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    email = Column(
        String,
        nullable=False,
        unique=True,
        index=True,
    )

    display_name = Column(String, nullable=True)

    # Null for accounts created through Google only.
    password_hash = Column(String, nullable=True)

    # Google account's unique subject identifier.
    google_sub = Column(
        String,
        nullable=True,
        unique=True,
        index=True,
    )

    # Firebase Authentication unique user identifier.
    firebase_uid = Column(
        String,
        nullable=True,
        unique=True,
        index=True,
    )

    is_active = Column(
        Boolean,
        nullable=False,
        default=True,
    )
    role = Column(
        String,
        nullable=False,
        default="student",
    )
    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

# =================================
# 2. Course model
# =================================

class CourseModel(Base):
    __tablename__ = "courses"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    title = Column(String, index=True)
    description = Column(String)


# =================================
# 3. Module model
# =================================

class ModuleModel(Base):
    __tablename__ = "modules"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    course_id = Column(
        Integer,
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
    )
    title = Column(String, nullable=False)
    description = Column(String)
    order_index = Column(Integer, nullable=False)


# =================================
# 4. Lesson model
# =================================

class LessonModel(Base):
    __tablename__ = "lessons"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    module_id = Column(
        Integer,
        ForeignKey("modules.id", ondelete="CASCADE"),
        nullable=False,
    )
    title = Column(String, nullable=False)
    description = Column(String)
    lesson_type = Column(String, default="concept")
    duration_minutes = Column(Integer, default=5)
    order_index = Column(Integer, nullable=False)


# =================================
# 5. Student lesson progress model
# =================================

class LessonProgressModel(Base):
    __tablename__ = "lesson_progress"

    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "lesson_id",
            name="uq_student_lesson_progress",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(String, nullable=False, index=True)
    lesson_id = Column(
        Integer,
        ForeignKey("lessons.id", ondelete="CASCADE"),
        nullable=False,
    )
    completed = Column(Integer, default=0, nullable=False)
    quiz_score = Column(Integer, default=0, nullable=False)
    total_questions = Column(Integer, default=0, nullable=False)


# =================================
# 6. XP transactions model
# =================================

class XPTransactionModel(Base):
    __tablename__ = "xp_transactions"

    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "lesson_id",
            name="uq_student_lesson_xp",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(String, nullable=False, index=True)
    lesson_id = Column(
        Integer,
        ForeignKey("lessons.id", ondelete="CASCADE"),
        nullable=False,
    )
    amount = Column(Integer, nullable=False, default=10)

# ==========================================
# Exercise Models
# ==========================================

class ExerciseModel(Base):
    __tablename__ = "exercises"

    id = Column(Integer, primary_key=True, index=True)

    lesson_id = Column(
        Integer,
        ForeignKey("lessons.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)

    instructions = Column(Text, nullable=True)
    starter_code = Column(Text, nullable=True)

    language = Column(String, nullable=False, default="python")
    difficulty = Column(String, nullable=False, default="beginner")

    xp_reward = Column(Integer, nullable=False, default=10)
    order_index = Column(Integer, nullable=False, default=1)


class TestCaseModel(Base):
    __tablename__ = "test_cases"

    id = Column(Integer, primary_key=True, index=True)

    exercise_id = Column(
        Integer,
        ForeignKey("exercises.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    input_data = Column(Text, nullable=False, default="")
    expected_output = Column(Text, nullable=False)

    is_hidden = Column(Boolean, nullable=False, default=False)


class SubmissionModel(Base):
    __tablename__ = "submissions"

    id = Column(Integer, primary_key=True, index=True)

    student_id = Column(String, nullable=False, index=True)

    exercise_id = Column(
        Integer,
        ForeignKey("exercises.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    code = Column(Text, nullable=False)

    status = Column(String, nullable=False, default="pending")

    output = Column(Text, nullable=True)
    stderr = Column(Text, nullable=True)

    score = Column(Integer, nullable=False, default=0)

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )


class ExerciseXPTransactionModel(Base):
    __tablename__ = "exercise_xp_transactions"

    id = Column(Integer, primary_key=True, index=True)

    student_id = Column(String, nullable=False)

    exercise_id = Column(
        Integer,
        ForeignKey("exercises.id", ondelete="CASCADE"),
        nullable=False
    )

    amount = Column(Integer, nullable=False, default=10)

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "exercise_id",
            name="unique_student_exercise_xp"
        ),
    )


# =================================
# 8. Student course learning level
# =================================

class StudentCourseLevelModel(Base):
    __tablename__ = "student_course_levels"

    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "course_id",
            name="uq_student_course_level",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    student_id = Column(
        String,
        nullable=False,
        index=True,
    )

    course_id = Column(
        Integer,
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # beginner, intermediate, advanced
    level = Column(
        String,
        nullable=False,
        default="beginner",
    )

    # manual or assessment
    source = Column(
        String,
        nullable=False,
        default="manual",
    )

    assessment_score = Column(Integer, nullable=True)
    assessment_total = Column(Integer, nullable=True)
    is_completed = Column(
        Boolean,
        nullable=False,
        default=False,
    )
    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

# ==========================================
# 9. Level Exam System Models
# ==========================================

# ------------------------------------------
# 9.1. Level Exam Model
# One exam per course and learning level.
# ------------------------------------------

class LevelExamModel(Base):
    __tablename__ = "level_exams"

    __table_args__ = (
        UniqueConstraint(
            "course_id",
            "level",
            name="uq_course_level_exam",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)

    course_id = Column(
        Integer,
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # beginner, intermediate, advanced
    level = Column(String, nullable=False)

    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)

    pass_score = Column(Integer, nullable=False, default=70)
    duration_minutes = Column(Integer, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )


# ------------------------------------------
# 9.2. Exam Question Model
# Stores theoretical and practical questions.
# ------------------------------------------

class ExamQuestionModel(Base):
    __tablename__ = "exam_questions"

    id = Column(Integer, primary_key=True, index=True)

    exam_id = Column(
        Integer,
        ForeignKey("level_exams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # multiple_choice, true_false, code_completion,
    # code_ordering, find_bug, problem_solving, voice_answer
    question_type = Column(String, nullable=False)

    question_text = Column(Text, nullable=False)

    # JSON string containing choices when needed.
    options_json = Column(Text, nullable=True)

    # Correct answer is backend-only.
    correct_answer = Column(Text, nullable=True)

    explanation = Column(Text, nullable=True)

    starter_code = Column(Text, nullable=True)

    points = Column(Integer, nullable=False, default=1)

    order_index = Column(Integer, nullable=False, default=1)

    is_active = Column(Boolean, nullable=False, default=True)


# ------------------------------------------
# 9.3. Exam Question Test Cases
# Used for code-based questions.
# ------------------------------------------

class ExamQuestionTestCaseModel(Base):
    __tablename__ = "exam_question_test_cases"

    id = Column(Integer, primary_key=True, index=True)

    question_id = Column(
        Integer,
        ForeignKey("exam_questions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    input_data = Column(Text, nullable=False, default="")

    expected_output = Column(Text, nullable=False)

    is_hidden = Column(Boolean, nullable=False, default=False)

    points = Column(Integer, nullable=False, default=1)


# ------------------------------------------
# 9.4. Exam Attempt Model
# Stores every student exam attempt.
# ------------------------------------------

class ExamAttemptModel(Base):
    __tablename__ = "exam_attempts"

    id = Column(Integer, primary_key=True, index=True)

    student_id = Column(
        String,
        nullable=False,
        index=True,
    )

    exam_id = Column(
        Integer,
        ForeignKey("level_exams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    score = Column(Integer, nullable=False, default=0)

    total_points = Column(Integer, nullable=False, default=0)

    percentage = Column(Float, nullable=False, default=0.0)

    passed = Column(Boolean, nullable=False, default=False)

    # in_progress or submitted
    status = Column(String, nullable=False, default="in_progress")

    started_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    submitted_at = Column(DateTime, nullable=True)


# ------------------------------------------
# 9.5. Exam Answer Model
# Stores each answer in an exam attempt.
# ------------------------------------------

class ExamAnswerModel(Base):
    __tablename__ = "exam_answers"

    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            "question_id",
            name="uq_attempt_question_answer",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)

    attempt_id = Column(
        Integer,
        ForeignKey("exam_attempts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    question_id = Column(
        Integer,
        ForeignKey("exam_questions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Textual, theoretical, or selected-option answer.
    answer_text = Column(Text, nullable=True)

    # Student code for practical questions.
    submitted_code = Column(Text, nullable=True)

    # Text converted from the student's voice answer.
    audio_transcript = Column(Text, nullable=True)

    is_correct = Column(Boolean, nullable=True)

    score = Column(Integer, nullable=False, default=0)

    feedback = Column(Text, nullable=True)

# =================================
# 7. Create missing database tables
# Existing database and data are preserved.
# =================================

Base.metadata.create_all(bind=engine)

# Safely add Firebase UID to existing users table.
with engine.begin() as connection:
    user_columns = {
        row[1] for row in connection.exec_driver_sql(
            'PRAGMA table_info(users)'
        ).fetchall()
    }
    if 'firebase_uid' not in user_columns:
        connection.exec_driver_sql(
            'ALTER TABLE users ADD COLUMN firebase_uid VARCHAR'
        )
    connection.exec_driver_sql(
        'CREATE UNIQUE INDEX IF NOT EXISTS ix_users_firebase_uid '
        'ON users (firebase_uid)'
    )
    # Safely add user role to existing users.
    if 'role' not in user_columns:
        connection.exec_driver_sql(
            "ALTER TABLE users ADD COLUMN role "
            "VARCHAR NOT NULL DEFAULT 'student'"
        )

    # Ensure existing users have the default student role.
    connection.exec_driver_sql(
        "UPDATE users SET role = 'student' "
        "WHERE role IS NULL OR role = ''"
    )
# Safely add is_completed to existing student_course_levels table.
with engine.begin() as connection:
    columns = connection.exec_driver_sql(
        "PRAGMA table_info(student_course_levels)"
    ).fetchall()

    column_names = [column[1] for column in columns]

    if "is_completed" not in column_names:
        connection.exec_driver_sql(
            "ALTER TABLE student_course_levels "
            "ADD COLUMN is_completed BOOLEAN NOT NULL DEFAULT 0"
        )
# =================================
# 8. FastAPI application
# =================================

app = FastAPI(
    title="CodeQuest Backend API",
    description="Backend API for the CodeQuest learning application",
    version="2.3.0",
)


# =================================
# 9. CORS configuration
# =================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =================================
# 10. Pydantic request models
# =================================

class CourseCreate(BaseModel):
    title: str
    description: str


class CourseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=1000)


class ModuleCreate(BaseModel):
    course_id: int
    title: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=1000)
    order_index: int = Field(default=1, ge=1)


class ModuleUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=1000)
    order_index: int | None = Field(default=None, ge=1)


class LessonCreate(BaseModel):
    module_id: int
    title: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=2000)
    lesson_type: str = "concept"
    duration_minutes: int = Field(default=5, ge=1)
    order_index: int = Field(default=1, ge=1)


class LessonUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    lesson_type: str | None = None
    duration_minutes: int | None = Field(default=None, ge=1)
    order_index: int | None = Field(default=None, ge=1)

class RegisterRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=80)


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=1, max_length=128)

class CodeRunRequest(BaseModel):
    code: str
    language_id: int = 1
class AITutorRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    code: str = Field(default="", max_length=10000)
    language: str = Field(default="python", max_length=30)
class ExerciseSubmitRequest(BaseModel):
    student_id: str = "demo_student"
    code: str

class CompleteLessonRequest(BaseModel):
    student_id: str = "demo_student"
    quiz_score: int
    total_questions: int


class TestCaseCreate(BaseModel):
    input_data: str = ""
    expected_output: str
    is_hidden: bool = False


class ExerciseCreate(BaseModel):
    lesson_id: int
    title: str
    description: str
    instructions: str | None = None
    starter_code: str | None = None
    language: str = "python"
    difficulty: str = "beginner"
    xp_reward: int = 10
    order_index: int = 1
    test_cases: list[TestCaseCreate]


class ExerciseUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    instructions: str | None = None
    starter_code: str | None = None
    language: str | None = None
    difficulty: str | None = None
    xp_reward: int | None = None
    order_index: int | None = None

# =================================
# Student placement request schemas
# =================================

class ManualPlacementRequest(BaseModel):
    course_id: int
    level: str = Field(
        pattern="^(beginner|intermediate|advanced)$"
    )

class PlacementAnswer(BaseModel):
    question_id: str
    option_id: str


class PlacementSubmitRequest(BaseModel):
    course_id: int
    answers: list[PlacementAnswer] = Field(
        min_length=6,
        max_length=6,
    )

# =================================
# Placement Questions Bank
# =================================

PLACEMENT_QUESTIONS = {

    "python": [
        {
            "id": "py1", "level": "beginner",
            "question": "Which function displays text in Python?",
            "options": [
                {"id": "a", "text": "print()"},
                {"id": "b", "text": "show()"},
                {"id": "c", "text": "display()"},
                {"id": "d", "text": "write()"},
            ],
            "correct": "a",
        },
        {
            "id": "py2", "level": "beginner",
            "question": "Which symbol is used to assign a value?",
            "options": [
                {"id": "a", "text": "=="},
                {"id": "b", "text": "="},
                {"id": "c", "text": "!="},
                {"id": "d", "text": ">"},
            ],
            "correct": "b",
        },
        {
            "id": "py3", "level": "intermediate",
            "question": "What does len([1, 2, 3]) return?",
            "options": [
                {"id": "a", "text": "2"},
                {"id": "b", "text": "4"},
                {"id": "c", "text": "3"},
                {"id": "d", "text": "1"},
            ],
            "correct": "c",
        },
        {
            "id": "py4", "level": "intermediate",
            "question": "Which keyword is used to define a function?",
            "options": [
                {"id": "a", "text": "function"},
                {"id": "b", "text": "define"},
                {"id": "c", "text": "func"},
                {"id": "d", "text": "def"},
            ],
            "correct": "d",
        },
        {
            "id": "py5", "level": "advanced",
            "question": "What is the main purpose of a Python decorator?",
            "options": [
                {"id": "a", "text": "To modify or extend a function's behavior"},
                {"id": "b", "text": "To create a database"},
                {"id": "c", "text": "To stop a loop"},
                {"id": "d", "text": "To declare an integer"},
            ],
            "correct": "a",
        },
        {
            "id": "py6", "level": "advanced",
            "question": "Which method is called when a new object is initialized?",
            "options": [
                {"id": "a", "text": "__start__()"},
                {"id": "b", "text": "__init__()"},
                {"id": "c", "text": "__new_object__()"},
                {"id": "d", "text": "initialize()"},
            ],
            "correct": "b",
        },
    ],

    "java": [
        {
            "id": "ja1", "level": "beginner",
            "question": "Which method is the entry point of a Java application?",
            "options": [
                {"id": "a", "text": "start()"},
                {"id": "b", "text": "run()"},
                {"id": "c", "text": "main()"},
                {"id": "d", "text": "begin()"},
            ],
            "correct": "c",
        },
        {
            "id": "ja2", "level": "beginner",
            "question": "Which keyword declares an integer variable in Java?",
            "options": [
                {"id": "a", "text": "int"},
                {"id": "b", "text": "number"},
                {"id": "c", "text": "integer"},
                {"id": "d", "text": "varint"},
            ],
            "correct": "a",
        },
        {
            "id": "ja3", "level": "intermediate",
            "question": "Which keyword is used to inherit a class in Java?",
            "options": [
                {"id": "a", "text": "implements"},
                {"id": "b", "text": "extends"},
                {"id": "c", "text": "inherits"},
                {"id": "d", "text": "super"},
            ],
            "correct": "b",
        },
        {
            "id": "ja4", "level": "intermediate",
            "question": "Which collection does not allow duplicate elements?",
            "options": [
                {"id": "a", "text": "ArrayList"},
                {"id": "b", "text": "LinkedList"},
                {"id": "c", "text": "Vector"},
                {"id": "d", "text": "HashSet"},
            ],
            "correct": "d",
        },
        {
            "id": "ja5", "level": "advanced",
            "question": "What is method overriding?",
            "options": [
                {"id": "a", "text": "Defining a method with the same signature in a subclass"},
                {"id": "b", "text": "Deleting a parent class"},
                {"id": "c", "text": "Creating multiple packages"},
                {"id": "d", "text": "Changing a variable type"},
            ],
            "correct": "a",
        },
        {
            "id": "ja6", "level": "advanced",
            "question": "Which concept allows one interface to have multiple implementations?",
            "options": [
                {"id": "a", "text": "Encapsulation"},
                {"id": "b", "text": "Compilation"},
                {"id": "c", "text": "Polymorphism"},
                {"id": "d", "text": "Casting"},
            ],
            "correct": "c",
        },
    ],

    "javascript": [
        {
            "id": "js1", "level": "beginner",
            "question": "Which keyword declares a block-scoped variable that can change?",
            "options": [
                {"id": "a", "text": "const"},
                {"id": "b", "text": "let"},
                {"id": "c", "text": "fixed"},
                {"id": "d", "text": "static"},
            ],
            "correct": "b",
        },
        {
            "id": "js2", "level": "beginner",
            "question": "Which command prints a message in the browser console?",
            "options": [
                {"id": "a", "text": "print()"},
                {"id": "b", "text": "echo()"},
                {"id": "c", "text": "console.log()"},
                {"id": "d", "text": "write()"},
            ],
            "correct": "c",
        },
        {
            "id": "js3", "level": "intermediate",
            "question": "Which array method creates a new array by transforming elements?",
            "options": [
                {"id": "a", "text": "map()"},
                {"id": "b", "text": "push()"},
                {"id": "c", "text": "pop()"},
                {"id": "d", "text": "shift()"},
            ],
            "correct": "a",
        },
        {
            "id": "js4", "level": "intermediate",
            "question": "Which operator checks both value and type equality?",
            "options": [
                {"id": "a", "text": "="},
                {"id": "b", "text": "=="},
                {"id": "c", "text": "!="},
                {"id": "d", "text": "==="},
            ],
            "correct": "d",
        },
        {
            "id": "js5", "level": "advanced",
            "question": "What does Promise.all() do?",
            "options": [
                {"id": "a", "text": "Runs only the first promise"},
                {"id": "b", "text": "Waits for all promises to fulfill, or rejects if one rejects"},
                {"id": "c", "text": "Converts promises into functions"},
                {"id": "d", "text": "Stops asynchronous execution"},
            ],
            "correct": "b",
        },
        {
            "id": "js6", "level": "advanced",
            "question": "What is a closure in JavaScript?",
            "options": [
                {"id": "a", "text": "A loop that never ends"},
                {"id": "b", "text": "A way to close the browser"},
                {"id": "c", "text": "A function that retains access to its lexical scope"},
                {"id": "d", "text": "A special array"},
            ],
            "correct": "c",
        },
    ],

    "cpp": [
        {
            "id": "cp1", "level": "beginner",
            "question": "Which header is commonly used for cout and cin?",
            "options": [
                {"id": "a", "text": "<iostream>"},
                {"id": "b", "text": "<string>"},
                {"id": "c", "text": "<math.h>"},
                {"id": "d", "text": "<vector>"},
            ],
            "correct": "a",
        },
        {
            "id": "cp2", "level": "beginner",
            "question": "Which statement displays text in C++?",
            "options": [
                {"id": "a", "text": "print()"},
                {"id": "b", "text": "echo"},
                {"id": "c", "text": "cout <<"},
                {"id": "d", "text": "display()"},
            ],
            "correct": "c",
        },
        {
            "id": "cp3", "level": "intermediate",
            "question": "Which keyword is used to create a class in C++?",
            "options": [
                {"id": "a", "text": "object"},
                {"id": "b", "text": "class"},
                {"id": "c", "text": "structclass"},
                {"id": "d", "text": "define"},
            ],
            "correct": "b",
        },
        {
            "id": "cp4", "level": "intermediate",
            "question": "Which container stores elements in a dynamic array?",
            "options": [
                {"id": "a", "text": "stack"},
                {"id": "b", "text": "queue"},
                {"id": "c", "text": "map"},
                {"id": "d", "text": "vector"},
            ],
            "correct": "d",
        },
        {
            "id": "cp5", "level": "advanced",
            "question": "What is a virtual function mainly used for?",
            "options": [
                {"id": "a", "text": "Runtime polymorphism"},
                {"id": "b", "text": "Declaring constants"},
                {"id": "c", "text": "Creating loops"},
                {"id": "d", "text": "Reading files only"},
            ],
            "correct": "a",
        },
        {
            "id": "cp6", "level": "advanced",
            "question": "What does std::move typically enable?",
            "options": [
                {"id": "a", "text": "Automatic thread creation"},
                {"id": "b", "text": "Converting every object to a pointer"},
                {"id": "c", "text": "Move semantics"},
                {"id": "d", "text": "Deleting all variables"},
            ],
            "correct": "c",
        },
    ],
}

def get_course_placement_language(course_title: str):
    title = course_title.strip().lower()

    if "python" in title:
        return "python"

    if "javascript" in title:
        return "javascript"

    if "java" in title:
        return "java"

    if "c++" in title or "cpp" in title:
        return "cpp"

    return None

# =================================
# 11. Database dependency
# =================================

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =================================
# Firebase Authentication
# =================================


def get_firebase_admin_app():
    try:
        return firebase_admin.get_app()

    except ValueError:
        service_account_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "firebase-service-account.json"
        )

        if not os.path.exists(service_account_path):
            raise RuntimeError(
                "Firebase Service Account file was not found."
            )

        firebase_credentials = credentials.Certificate(
            service_account_path
        )

        return firebase_admin.initialize_app(
            firebase_credentials,
            {"projectId": "codequest-android-debug"}
        )

def get_current_firebase_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(firebase_bearer),
    db: Session = Depends(get_db),
):
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Firebase authentication token is required.",
        )

    token = credentials.credentials.strip()

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Firebase authentication token is required.",
        )

    try:
        app = get_firebase_admin_app()

        decoded_token = firebase_auth.verify_id_token(
            token,
            app=app,
            check_revoked=True,
        )

        firebase_uid = decoded_token.get("uid")

        if not firebase_uid:
            raise HTTPException(
                status_code=401,
                detail="Invalid Firebase authentication token.",
            )

        current_user = db.query(UserModel).filter(
            UserModel.firebase_uid == firebase_uid
        ).first()

        if not current_user:
            raise HTTPException(
                status_code=401,
                detail="Account not found. Please sign in again.",
            )

        if not current_user.is_active:
            raise HTTPException(
                status_code=403,
                detail="This account is inactive. Please contact the administrator.",
            )

        return current_user

    except HTTPException:
        raise

    except Exception as exc:
        print(
            f"[Firebase Auth] Verification failed: "
            f"{type(exc).__name__}: {exc}"
        )
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired Firebase authentication token.",
        )
  
def require_expert(
    current_user: UserModel = Depends(get_current_firebase_user),
):
    if current_user.role not in ("expert", "admin"):
        raise HTTPException(
            status_code=403,
            detail="Expert access required.",
        )

    return current_user



# =================================
# Authentication helpers
# =================================

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)

    password_hash = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )

    return f"{salt.hex()}:{password_hash.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, hash_hex = stored_hash.split(":", 1)

        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(hash_hex)

        actual_hash = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
            dklen=32,
        )

        return hmac.compare_digest(actual_hash, expected_hash)

    except (ValueError, TypeError):
        return False


def create_access_token(user_id: str) -> str:
    secret = os.getenv("CODEQUEST_JWT_SECRET")

    if not secret:
        raise HTTPException(
            status_code=500,
            detail="Authentication server configuration is missing.",
        )

    now = datetime.utcnow()

    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(days=7),
    }

    return jwt.encode(
        payload,
        secret,
        algorithm="HS256",
    )


def create_auth_response(user: UserModel):
    return {
        "access_token": create_access_token(user.id),
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
        },
    }
    # =================================
# Authentication endpoints
# =================================

@app.post("/auth/register")
def register_user(
    request: RegisterRequest,
    db: Session = Depends(get_db),
):
    email = request.email.strip().lower()

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(
            status_code=400,
            detail="Invalid email address.",
        )

    existing_user = (
        db.query(UserModel)
        .filter(UserModel.email == email)
        .first()
    )

    if existing_user:
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists.",
        )

    user = UserModel(
        id=str(uuid.uuid4()),
        email=email,
        display_name=request.display_name,
        password_hash=hash_password(request.password),
    )

    try:
        db.add(user)
        db.commit()
        db.refresh(user)

    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists.",
        )

    return create_auth_response(user)


@app.post("/auth/login")
def login_user(
    request: LoginRequest,
    db: Session = Depends(get_db),
):
    email = request.email.strip().lower()

    user = (
        db.query(UserModel)
        .filter(UserModel.email == email)
        .first()
    )

    if (
        not user
        or not user.password_hash
        or not verify_password(
            request.password,
            user.password_hash,
        )
    ):
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=403,
            detail="This account is disabled.",
        )

    return create_auth_response(user)
# =================================

# =================================
# Firebase authenticated user
# =================================

@app.get("/auth/me")
def get_authenticated_user(
    current_user: UserModel = Depends(get_current_firebase_user),
):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "display_name": current_user.display_name,
        "role": current_user.role,
        "is_active": current_user.is_active,
        "created_at": (
            current_user.created_at.isoformat()
            if current_user.created_at
            else None
        ),
    }
# 12. Test endpoint
# =================================

@app.get("/test")
def test_server():
    return {"message": "CodeQuest server is working!"}



# =================================
# 13. Initialize default courses
# =================================


@app.on_event("startup")
def initialize_courses():
    db = SessionLocal()

    try:
        default_courses = [
            {
                "title": "Python Programming",
                "description": "Learn programming basics, variables, and functions using Python.",
            },
            {
                "title": "Java (OOP)",
                "description": "Learn object-oriented programming concepts, classes, and inheritance using Java.",
            },
            {
                "title": "JavaScript Essentials",
                "description": "Learn JavaScript fundamentals and how to add interactivity to web pages.",
            },
            {
                "title": "C++ Programming",
                "description": "Learn C++ programming fundamentals, object-oriented programming, and practical projects.",
            },
        ]

        added_courses = 0

        for item in default_courses:
            existing_course = db.query(CourseModel).filter(
                CourseModel.title == item["title"]
            ).first()

            if existing_course is None:
                course = CourseModel(
                    title=item["title"],
                    description=item["description"],
                )

                db.add(course)
                added_courses += 1

        db.commit()

        print(
            f"[CodeQuest] Course initialization completed. "
            f"New courses added: {added_courses}"
        )

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()

# =================================
# 14. Initialize Python learning content
# =================================

@app.on_event("startup")
def initialize_python_content():
    db = SessionLocal()
    try:
        python_course = db.query(CourseModel).filter(
            CourseModel.title == "Python Programming"
        ).first()

        if python_course is None:
            print("Python course not found.")
            return

        python_modules = [
            {
                "title": "Getting Started with Python",
                "description": "Learn about programming, the Python language, and setting up the development environment.",
                "lessons": [
                    ("What is Programming?", "Understand programming and how to write instructions for a computer.", "concept", 5),
                    ("Introduction to Python", "Learn about Python and its applications.", "concept", 5),
                    ("Your First Python Program", "Write your first program using print().", "practice", 10),
                ],
            },
            {
                "title": "Variables and Data Types",
                "description": "Learn how to store data and work with different data types.",
                "lessons": [
                    ("What are Variables?", "Understand variables and how to create them.", "concept", 5),
                    ("Numbers and Strings", "Work with numbers and strings.", "concept", 8),
                    ("Booleans and Type Conversion", "Learn about Boolean values and converting between data types.", "practice", 10),
                ],
            },
            {
                "title": "Input, Output and Operators",
                "description": "Receive input, display results, and perform operations.",
                "lessons": [
                    ("Output with print()", "Display text and results using print().", "concept", 5),
                    ("User Input", "Receive input from users using input().", "practice", 8),
                    ("Python Operators", "Learn arithmetic, comparison, and logical operators.", "practice", 10),
                ],
            },
            {
                "title": "Conditional Statements",
                "description": "Make decisions within programs.",
                "lessons": [
                    ("The if Statement", "Execute instructions based on a condition.", "concept", 7),
                    ("if, elif and else", "Use multiple conditional statements.", "concept", 8),
                    ("Conditional Practice", "Practice making decisions in programs.", "practice", 10),
                ],
            },
            {
                "title": "Loops",
                "description": "Repeat instructions using loops.",
                "lessons": [
                    ("The for Loop", "Learn how to use the for loop.", "concept", 8),
                    ("The while Loop", "Learn how to use the while loop.", "concept", 8),
                    ("break and continue", "Control the execution flow of loops.", "practice", 10),
                ],
            },
            {
                "title": "Data Structures",
                "description": "Organize data and work with collections of values.",
                "lessons": [
                    ("Lists", "Create lists and access their elements.", "concept", 8),
                    ("Tuples and Sets", "Learn about tuples and sets.", "concept", 8),
                    ("Dictionaries", "Store data using key-value pairs.", "practice", 10),
                ],
            },
            {
                "title": "Functions",
                "description": "Divide programs into reusable functions.",
                "lessons": [
                    ("Creating Functions", "Create functions using def.", "concept", 8),
                    ("Parameters and Return", "Pass parameters and return results.", "concept", 10),
                    ("Functions Practice", "Practice creating and using functions.", "practice", 12),
                ],
            },
            {
                "title": "Object-Oriented Programming",
                "description": "An introduction to object-oriented programming (OOP).",
                "lessons": [
                    ("Classes and Objects", "Understand classes and objects.", "concept", 10),
                    ("Attributes and Methods", "Learn about attributes and methods inside a class.", "concept", 10),
                    ("Constructors and __init__", "Create objects and initialize their attributes.", "practice", 12),
                    ("Inheritance", "Understand inheritance and code reuse.", "concept", 12),
                ],
            },
            {
                "title": "Mini Projects",
                "description": "Apply the concepts you have learned in small projects.",
                "lessons": [
                    ("Simple Calculator", "Create a simple calculator.", "project", 20),
                    ("Number Guessing Game", "Create a number guessing game.", "project", 20),
                    ("Student Management Mini Project", "Create a simple project to manage student information.", "project", 30),
                ],
            },
        ]

        for module_index, module_data in enumerate(python_modules, start=1):
            module = db.query(ModuleModel).filter(
                ModuleModel.course_id == python_course.id,
                ModuleModel.order_index == module_index,
            ).first()

            if module is None:
                module = ModuleModel(
                    course_id=python_course.id,
                    title=module_data["title"],
                    description=module_data["description"],
                    order_index=module_index,
                )
                db.add(module)
                db.flush()

            for lesson_index, lesson_data in enumerate(
                module_data["lessons"], start=1
            ):
                existing_lesson = db.query(LessonModel).filter(
                    LessonModel.module_id == module.id,
                    LessonModel.order_index == lesson_index,
                ).first()

                if existing_lesson is None:
                    db.add(
                        LessonModel(
                            module_id=module.id,
                            title=lesson_data[0],
                            description=lesson_data[1],
                            lesson_type=lesson_data[2],
                            duration_minutes=lesson_data[3],
                            order_index=lesson_index,
                        )
                    )

        db.commit()
        print("Python learning content initialized successfully.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()



# ==========================================
# 14.5. Initialize Python practical exercises
# Add exercises only when a lesson has none.
# Existing exercises and XP transactions are preserved.
# ==========================================

PYTHON_PRACTICAL_EXERCISES = {

    "What is Programming?": {
        "title": "Your First Instructions",
        "description": "Write a program that displays three instructions on the screen.",
        "instructions": "Use print() to display the following lines in order:\nWelcome to CodeQuest\nI am learning Python\nProgramming is fun!",
        "starter_code": "# Write your code below\n",
        "tests": [
            ("", "Welcome to CodeQuest\nI am learning Python\nProgramming is fun!", False),
        ],
    },

    "Introduction to Python": {
        "title": "Python Introduction",
        "description": "Write a program that displays the programming language name and a welcome message.",
        "instructions": "Print Python on the first line and Welcome on the second line.",
        "starter_code": "# Display the required messages\n",
        "tests": [
            ("", "Python\nWelcome", False),
        ],
    },

    "Your First Python Program": {
        "title": "Personal Greeting",
        "description": "Write a program that receives the user's name and prints a greeting message.",
        "instructions": "Read the name using input(), then print Hello followed by the name.",
        "starter_code": "name = input()\n# Print the greeting\n",
        "tests": [
            ("Sara\n", "Hello Sara", False),
            ("Omar\n", "Hello Omar", True),
        ],
    },

    "What are Variables?": {
        "title": "Student Information",
        "description": "Use variables to store the student's name and age.",
        "instructions": "Read the name and then the age, store them in two variables, and print them in this format:\nName: Sara\nAge: 20",
        "starter_code": "name = input()\nage = input()\n# Print the information\n",
        "tests": [
            ("Sara\n20\n", "Name: Sara\nAge: 20", False),
            ("Omar\n18\n", "Name: Omar\nAge: 18", True),
        ],
    },

    "Numbers and Strings": {
        "title": "Number and Text Converter",
        "description": "Work with numbers and strings using type conversion.",
        "instructions": "Read two integers, then print their sum on one line and their product on the next line.",
        "starter_code": "a = int(input())\nb = int(input())\n# Calculate and print\n",
        "tests": [
            ("5\n3\n", "8\n15", False),
            ("10\n4\n", "14\n40", True),
        ],
    },

    "Booleans and Type Conversion": {
        "title": "Age Checker",
        "description": "Use int conversion and Boolean values.",
        "instructions": "Read the age and convert it to an integer. Print True if the age is 18 or older; otherwise, print False.",
        "starter_code": "age = int(input())\n# Check the age\n",
        "tests": [
            ("20\n", "True", False),
            ("15\n", "False", True),
        ],
    },

    "Output with print()": {
        "title": "Formatted Output",
        "description": "Practice displaying multiple values using print().",
        "instructions": "Print the programming name CodeQuest, then print the number 100 on a separate line.",
        "starter_code": "# Use print()\n",
        "tests": [
            ("", "CodeQuest\n100", False),
        ],
    },

    "User Input": {
        "title": "Welcome User",
        "description": "Receive the user's name and display a welcome message.",
        "instructions": "Read the user's name and print Welcome followed by the name.",
        "starter_code": "name = input()\n# Print welcome message\n",
        "tests": [
            ("Lina\n", "Welcome Lina", False),
            ("Ahmad\n", "Welcome Ahmad", True),
        ],
    },

    "Python Operators": {
        "title": "Basic Calculator",
        "description": "Use basic arithmetic operations.",
        "instructions": "Read two integers and print the results of addition, subtraction, and multiplication, each on a separate line.",
        "starter_code": "a = int(input())\nb = int(input())\n# Perform the operations\n",
        "tests": [
            ("8\n2\n", "10\n6\n16", False),
            ("7\n3\n", "10\n4\n21", True),
        ],
    },

    "The if Statement": {
        "title": "Positive Number",
        "description": "Use if to check a number.",
        "instructions": "Read an integer. If it is greater than zero, print Positive.",
        "starter_code": "number = int(input())\n# Write your if statement\n",
        "tests": [
            ("5\n", "Positive", False),
            ("-3\n", "", True),
        ],
    },

    "if, elif and else": {
        "title": "Grade Evaluation",
        "description": "Use multiple conditions to determine the grade.",
        "instructions": "Read a grade out of 100:\n90 or above: Excellent\n70 or above: Good\nBelow 70: Needs Practice",
        "starter_code": "grade = int(input())\n# Check the grade\n",
        "tests": [
            ("95\n", "Excellent", False),
            ("80\n", "Good", False),
            ("60\n", "Needs Practice", True),
        ],
    },

    "Conditional Practice": {
        "title": "Even or Odd",
        "description": "Apply conditional statements to check whether a number is even or odd.",
        "instructions": "Read an integer. Print Even if it is even; otherwise, print Odd.",
        "starter_code": "number = int(input())\n# Check even or odd\n",
        "tests": [
            ("8\n", "Even", False),
            ("7\n", "Odd", True),
        ],
    },

    "The for Loop": {
        "title": "Sum from One to N",
        "description": "Use a for loop to calculate the sum of numbers from 1 to the entered number.",
        "instructions": "Read a positive integer n, calculate the sum of numbers from 1 to n, and print the result.",
        "starter_code": "n = int(input())\ntotal = 0\n# Use a for loop\n",
        "tests": [
            ("5\n", "15", False),
            ("10\n", "55", True),
        ],
    },

    "The while Loop": {
        "title": "Countdown",
        "description": "Use while to print a countdown.",
        "instructions": "Read an integer n and print the numbers from n down to 1, each on a separate line.",
        "starter_code": "n = int(input())\n# Use a while loop\n",
        "tests": [
            ("3\n", "3\n2\n1", False),
            ("5\n", "5\n4\n3\n2\n1", True),
        ],
    },

    "break and continue": {
        "title": "Skip Multiples of Three",
        "description": "Use continue to skip numbers divisible by 3.",
        "instructions": "Read the number n and print the numbers from 1 to n, excluding numbers divisible by 3.",
        "starter_code": "n = int(input())\n# Use a loop and continue\n",
        "tests": [
            ("7\n", "1\n2\n4\n5\n7", False),
            ("5\n", "1\n2\n4\n5", True),
        ],
    },

    "Lists": {
        "title": "List Calculator",
        "description": "Practice using lists and calculating the sum of their elements.",
        "instructions": "Read three integers, store them in a list, and print the sum of the list elements.",
        "starter_code": "numbers = []\n# Read three numbers and calculate the sum\n",
        "tests": [
            ("2\n3\n4\n", "9", False),
            ("10\n20\n30\n", "60", True),
        ],
    },

    "Tuples and Sets": {
        "title": "Unique Numbers",
        "description": "Use set to remove duplicate values.",
        "instructions": "Read three integers, store them in a set, and print the number of unique elements.",
        "starter_code": "numbers = []\n# Create a set and print its length\n",
        "tests": [
            ("1\n2\n2\n", "2", False),
            ("5\n5\n5\n", "1", True),
        ],
    },

    "Dictionaries": {
        "title": "Student Dictionary",
        "description": "Use dictionaries to store student information.",
        "instructions": "Read the student's name and grade, create a dictionary containing name and grade, and print the name value followed by the grade value on two separate lines.",
        "starter_code": "name = input()\ngrade = input()\n# Create and use a dictionary\n",
        "tests": [
            ("Sara\n95\n", "Sara\n95", False),
            ("Omar\n80\n", "Omar\n80", True),
        ],
    },

    "Creating Functions": {
        "title": "Create a Square Function",
        "description": "Create a simple function using def.",
        "instructions": "Create a function named square that accepts a number and returns its square using return. Read a number and print the result.",
        "starter_code": "def square(number):\n    # Return the square\n    pass\n\nnumber = int(input())\nprint(square(number))\n",
        "tests": [
            ("4\n", "16", False),
            ("7\n", "49", True),
        ],
    },

    "Parameters and Return": {
        "title": "Add Two Numbers Function",
        "description": "Use parameters and return to produce a result.",
        "instructions": "Create a function named add that accepts two numbers and returns their sum. Read the two numbers and print the result.",
        "starter_code": "def add(a, b):\n    # Return the sum\n    pass\n\na = int(input())\nb = int(input())\nprint(add(a, b))\n",
        "tests": [
            ("4\n6\n", "10", False),
            ("10\n15\n", "25", True),
        ],
    },

    "Functions Practice": {
        "title": "Find the Larger Number",
        "description": "Use functions and conditional statements together.",
        "instructions": "Create a function named larger that accepts two numbers and returns the larger one. Read the two numbers and print the result.",
        "starter_code": "def larger(a, b):\n    # Return the larger number\n    pass\n\na = int(input())\nb = int(input())\nprint(larger(a, b))\n",
        "tests": [
            ("8\n5\n", "8", False),
            ("3\n9\n", "9", True),
        ],
    },

    "Classes and Objects": {
        "title": "Create a Student Object",
        "description": "Create a class and an object from it.",
        "instructions": "Create a class named Student, then create an object from it and print the word Student.",
        "starter_code": "class Student:\n    pass\n\n# Create an object\n",
        "tests": [
            ("", "Student", False),
        ],
    },

    "Attributes and Methods": {
        "title": "Student Method",
        "description": "Practice using attributes and methods inside a class.",
        "instructions": "Create a Student class containing a set_name method that accepts a name and stores it in self.name. Create an object and print the name.",
        "starter_code": "class Student:\n    def set_name(self, name):\n        # Store the name\n        pass\n\nstudent = Student()\nstudent.set_name(input())\nprint(student.name)\n",
        "tests": [
            ("Lina\n", "Lina", False),
            ("Ahmad\n", "Ahmad", True),
        ],
    },

    "Constructors and __init__": {
        "title": "Student Constructor",
        "description": "Use __init__ to initialize object attributes.",
        "instructions": "Create a Student class containing an __init__ method that accepts a name and stores it in self.name. Read the name, create an object, and print the name.",
        "starter_code": "class Student:\n    def __init__(self, name):\n        # Initialize the name\n        pass\n\nstudent = Student(input())\nprint(student.name)\n",
        "tests": [
            ("Mona\n", "Mona", False),
            ("Omar\n", "Omar", True),
        ],
    },

    "Inheritance": {
        "title": "Animal Inheritance",
        "description": "Apply inheritance between a parent class and a child class.",
        "instructions": "Create an Animal class containing a speak method that prints Sound. Create a Cat class that inherits from Animal, then create an object and call speak.",
        "starter_code": "class Animal:\n    def speak(self):\n        print('Sound')\n\n# Create a child class\n\n",
        "tests": [
            ("", "Sound", False),
        ],
    },

    "Simple Calculator": {
        "title": "Calculator Project",
        "description": "Create a simple calculator using conditional statements.",
        "instructions": "Read two numbers, then an operation symbol (+, -, or *), and print the result. Assume the entered operation is valid.",
        "starter_code": "a = float(input())\nb = float(input())\noperation = input()\n# Perform the selected operation\n",
        "tests": [
            ("8\n2\n+\n", "10.0", False),
            ("8\n2\n*\n", "16.0", True),
            ("8\n2\n-\n", "6.0", True),
        ],
    },

    "Number Guessing Game": {
        "title": "Guess the Number",
        "description": "Apply conditional statements to compare the user's guess with the target number.",
        "instructions": "Assume the secret number is 7. Read the user's guess, then print Correct if it is correct, Too low if it is smaller, or Too high if it is larger.",
        "starter_code": "secret = 7\nguess = int(input())\n# Compare the guess\n",
        "tests": [
            ("7\n", "Correct", False),
            ("5\n", "Too low", True),
            ("9\n", "Too high", True),
        ],
    },

    "Student Management Mini Project": {
        "title": "Student Grade Manager",
        "description": "Combine dictionaries, input, and output.",
        "instructions": "Read a student's name and grade, store them in a dictionary using the keys name and grade, then print:\nStudent: Sara\nGrade: 90",
        "starter_code": "name = input()\ngrade = input()\n# Create the student dictionary and print its values\n",
        "tests": [
            ("Sara\n90\n", "Student: Sara\nGrade: 90", False),
            ("Omar\n85\n", "Student: Omar\nGrade: 85", True),
        ],
    },
}


@app.on_event("startup")
def initialize_python_exercises():
    db = SessionLocal()

    added_count = 0
    skipped_count = 0

    try:
        python_course = db.query(CourseModel).filter(
            CourseModel.title == "Python Programming"
        ).first()

        if python_course is None:
            print("[CodeQuest] Python course not found.")
            return

        lessons = (
            db.query(LessonModel)
            .join(ModuleModel, LessonModel.module_id == ModuleModel.id)
            .filter(ModuleModel.course_id == python_course.id)
            .all()
        )


        for lesson in lessons:

            exercise_data = PYTHON_PRACTICAL_EXERCISES.get(
                lesson.title
            )

            # Ignore lessons not included in the exercise list.
            if exercise_data is None:
                continue

            # Do not duplicate an exercise already assigned to this lesson.
            existing_exercise = db.query(ExerciseModel).filter(
                ExerciseModel.lesson_id == lesson.id
            ).first()

            if existing_exercise is not None:
                skipped_count += 1
                continue

            exercise = ExerciseModel(
                lesson_id=lesson.id,
                title=exercise_data["title"],
                description=exercise_data["description"],
                instructions=exercise_data["instructions"],
                starter_code=exercise_data["starter_code"],
                language="python",
                difficulty="beginner",
                xp_reward=10,
                order_index=1,
            )

            db.add(exercise)
            db.flush()

            for test in exercise_data["tests"]:
                input_data, expected_output, is_hidden = test

                test_case = TestCaseModel(
                    exercise_id=exercise.id,
                    input_data=input_data,
                    expected_output=expected_output,
                    is_hidden=is_hidden,
                )

                db.add(test_case)

            added_count += 1

        db.commit()

        print(
            f"[CodeQuest] Python exercises initialized. "
            f"Added: {added_count}, skipped: {skipped_count}"
        )

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


@app.on_event("startup")
def initialize_java_content():
    global java_modules

    
java_modules = [
    {
        "title": "Introduction to Java",
        "description": "Learn about Java and how to write and run programs.",
        "lessons": [
            ("What is Java?", "Understand Java, its uses, and its features."),
            ("Java Development Environment", "Learn about the JDK, JVM, and Java development environment."),
            ("Your First Java Program", "Write and run your first Java program using main and System.out.println.")
        ]
    },
    {
        "title": "Variables and Data Types",
        "description": "Store data and learn about data types in Java.",
        "lessons": [
            ("Variables in Java", "Learn how to declare variables, name them, and assign values."),
            ("Primitive Data Types", "Learn about int, double, char, boolean, and other primitive data types."),
            ("Strings and Type Casting", "Work with strings and convert between data types.")
        ]
    },
    {
        "title": "Input, Output and Operators",
        "description": "Read user input, display results, and use operators.",
        "lessons": [
            ("Output with System.out.println", "Display text and values using print statements."),
            ("User Input with Scanner", "Read user input using Scanner."),
            ("Java Operators", "Learn arithmetic, comparison, and logical operators.")
        ]
    },
    {
        "title": "Conditional Statements",
        "description": "Make decisions using conditional statements.",
        "lessons": [
            ("The if Statement", "Execute instructions when a condition is true."),
            ("if, else and else if", "Use multiple conditional branches."),
            ("Switch Statement", "Choose an execution path using switch.")
        ]
    },
    {
        "title": "Loops",
        "description": "Repeat instructions and control loops.",
        "lessons": [
            ("The for Loop", "Use for to repeat instructions."),
            ("The while and do-while Loops", "Repeat instructions using while and do-while."),
            ("break and continue", "Control loops by stopping repetition or skipping an iteration.")
        ]
    },
    {
        "title": "Arrays and Strings",
        "description": "Store collections of data and work with strings.",
        "lessons": [
            ("One-Dimensional Arrays", "Create arrays and access their elements."),
            ("Array Traversal and Operations", "Iterate through array elements and perform operations on them."),
            ("String Methods", "Use length, charAt, equals, and substring.")
        ]
    },
    {
        "title": "Methods",
        "description": "Create methods, organize programs, and reuse code.",
        "lessons": [
            ("Creating Methods", "Define and call methods in Java."),
            ("Parameters and Return Values", "Pass parameters and return values."),
            ("Method Overloading and Scope", "Understand method overloading and variable scope.")
        ]
    },
    {
        "title": "Object-Oriented Programming",
        "description": "Apply object-oriented programming concepts.",
        "lessons": [
            ("Classes and Objects", "Create classes and objects."),
            ("Constructors and Encapsulation", "Use constructors and encapsulate data."),
            ("Inheritance and Polymorphism", "Understand inheritance and polymorphism.")
        ]
    },
    {
        "title": "Mini Projects",
        "description": "Apply Java concepts in small practical projects.",
        "lessons": [
            ("Simple Calculator", "Create a calculator using conditional statements and methods."),
            ("Student Grade Manager", "Manage student names and grades using arrays."),
            ("Bank Account System", "Simulate a bank account using classes and objects.")
        ]
    }
]

JAVA_EXERCISE_SEEDS = {
    "What is Java?": (
        "Print a short introduction to Java.",
        "Write a program that prints: Java is a programming language",
        "", "Java is a programming language", 10
    ),
    "Java Development Environment": (
        "Print a message confirming that Java is ready.",
        "Write a program that prints: Java Ready",
        "", "Java Ready", 10
    ),
    "Your First Java Program": (
        "Write your first Java program.",
        "Print Hello CodeQuest",
        "", "Hello CodeQuest", 10
    ),
    "Variables in Java": (
        "Create two integer variables and print their sum.",
        "Create two variables with values 10 and 5, then print their sum.",
        "", "15", 10
    ),
    "Primitive Data Types": (
        "Create an integer variable and print its value.",
        "Create an int variable with the value 42 and print it.",
        "", "42", 10
    ),
    "Strings and Type Casting": (
        "Print a label and an integer value.",
        "Create a String named Age and an integer with the value 20, then print Age: 20.",
        "", "Age: 20", 10
    ),
    "Output with System.out.println": (
        "Print a welcome message.",
        "Use System.out.println to print Welcome.",
        "", "Welcome", 10
    ),
    "User Input with Scanner": (
        "Read a name and print a greeting.",
        "Use Scanner to read a name from the user, then print Hello followed by the name.",
        "Sara\n", "Hello Sara", 15
    ),
    "Java Operators": (
        "Calculate the result of an arithmetic expression.",
        "Calculate 10 + 5 using Java operators and print the result.",
        "", "15", 10
    ),
    "The if Statement": (
        "Check whether a person is eligible.",
        "If the age is 20 or older, print Eligible. Use the variable age = 20.",
        "", "Eligible", 10
    ),
    "if, else and else if": (
        "Determine the result of a student's mark.",
        "Use the grade 75: print Excellent if it is 90 or above, Passed if it is 50 or above, otherwise print Failed.",
        "", "Passed", 10
    ),
    "Switch Statement": (
        "Print a day name using switch.",
        "Use switch to print Monday when the value of day is 2.",
        "", "Monday", 10
    ),
    "The for Loop": (
        "Print numbers from 1 to 5.",
        "Use for to print numbers from 1 to 5, each on a separate line.",
        "", "1\n2\n3\n4\n5", 10
    ),
    "The while and do-while Loops": (
        "Print numbers from 1 to 3 using a loop.",
        "Use while to print numbers from 1 to 3.",
        "", "1\n2\n3", 10
    ),
    "break and continue": (
        "Skip number 3 in a loop.",
        "Print numbers from 1 to 5, skipping number 3 using continue.",
        "", "1\n2\n4\n5", 10
    ),
    "One-Dimensional Arrays": (
        "Print the first array element.",
        "Create an array {10, 20, 30} and print the first element.",
        "", "10", 10
    ),
    "Array Traversal and Operations": (
        "Calculate the sum of an array.",
        "Create an array {4, 5, 6} and calculate the sum of its elements using a loop.",
        "", "15", 15
    ),
    "String Methods": (
        "Convert a string to uppercase.",
        "Convert the string java to uppercase using toUpperCase.",
        "", "JAVA", 10
    ),
    "Creating Methods": (
        "Create and call a greeting method.",
        "Create a method named greet that prints Hello, then call it.",
        "", "Hello", 10
    ),
    "Parameters and Return Values": (
        "Create a method that adds two numbers.",
        "Create an add method that accepts two numbers and returns their sum. Test it with the values 4 and 5.",
        "", "9", 15
    ),
    "Method Overloading and Scope": (
        "Overload an add method.",
        "Create two methods named add: one accepts int and the other accepts double. Print the results of add(2,3) and then add(2.5,3.0).",
        "", "5\n5.5", 15
    ),
    "Classes and Objects": (
        "Create a Student class and an object.",
        "Create a class named Student containing name, create an object named Ali, and print its name.",
        "", "Ali", 15
    ),
    "Constructors and Encapsulation": (
        "Create an encapsulated account.",
        "Create a class with a private balance, a constructor that initializes the balance to 100, and a getBalance method to display it.",
        "", "100.0", 15
    ),
    "Inheritance and Polymorphism": (
        "Override a method in a child class.",
        "Create Animal and Cat classes, and make Cat override sound to print Meow.",
        "", "Meow", 15
    ),
    "Simple Calculator": (
        "Build a calculator for two numbers.",
        "Use Scanner to read two numbers and an operation symbol (+, -, *, or /), then print the result. Test addition.",
        "8\n2\n+\n", "10.0", 20
    ),
    "Student Grade Manager": (
        "Calculate the average of student marks.",
        "Create an array of grades {80, 90, 70}, calculate the average, and print it.",
        "", "80.0", 20
    ),
    "Bank Account System": (
        "Create a simple bank account.",
        "Create a BankAccount with an initial balance of 200, withdraw 50, and print the remaining balance.",
        "", "150.0", 20
    ),
}

# =================================
# JavaScript Practical Exercises
# =================================

JAVASCRIPT_PRACTICAL_EXERCISES = {

    "What is JavaScript?": {
        "title": "First JavaScript Output",
        "description": "Write a program that displays a welcome message.",
        "instructions": (
            "Use console.log() to display the following text exactly:\n"
            "Welcome to CodeQuest!"
        ),
        "starter_code": "// Write your code below\n",
        "tests": [
            ("", "Welcome to CodeQuest!", False),
        ],
    },

    "Variables with let and const": {
        "title": "Create Your Variables",
        "description": "Practice creating and using variables.",
        "instructions": (
            "Create a variable named name with the value Sara, "
            "and a variable named age with the value 20.\n"
            "Print the name and then the age, each on a separate line."
        ),
        "starter_code": "// Create your variables\n",
        "tests": [
            ("", "Sara\n20", False),
        ],
    },

    "Arithmetic Operators": {
        "title": "Calculate Two Numbers",
        "description": "Use arithmetic operations in JavaScript.",
        "instructions": (
            "Create two variables: a = 12 and b = 4.\n"
            "Print the results of addition, subtraction, multiplication, "
            "and division, each on a separate line."
        ),
        "starter_code": "// Write your calculation\n",
        "tests": [
            ("", "16\n8\n48\n3", False),
        ],
    },

    "if, else and else if": {
        "title": "Check Student Result",
        "description": "Use conditional statements to determine a student's result.",
        "instructions": (
            "Create a variable named mark with the value 75.\n"
            "If the grade is 50 or above, print Pass; otherwise, print Fail."
        ),
        "starter_code": "const mark = 75;\n\n// Write your condition\n",
        "tests": [
            ("", "Pass", False),
        ],
    },

    "The for Loop": {
        "title": "Print Numbers",
        "description": "Use a for loop to print numbers.",
        "instructions": (
            "Use a for loop to print numbers from 1 to 5, "
            "each on a separate line."
        ),
        "starter_code": "// Write your loop\n",
        "tests": [
            ("", "1\n2\n3\n4\n5", False),
        ],
    },

    "Creating and Accessing Arrays": {
        "title": "Work with an Array",
        "description": "Create an array and print its elements.",
        "instructions": (
            "Create an array named fruits containing "
            "apple, banana, and orange.\n"
            "Print the first element and then the array length."
        ),
        "starter_code": "// Create your array\n",
        "tests": [
            ("", "apple\n3", False),
        ],
    },

    "Creating Functions": {
        "title": "Create a Greeting Function",
        "description": "Write a function that accepts a name and displays a greeting message.",
        "instructions": (
            "Create a function named greet that accepts name.\n"
            "Print Hello followed by the name.\n"
            "Call the function using Sara."
        ),
        "starter_code": "// Create your function\n",
        "tests": [
            ("", "Hello Sara", False),
        ],
    },

    "Classes and Objects": {
        "title": "Create a Student Class",
        "description": "Practice creating a class and an object from it.",
        "instructions": (
            "Create a class named Student.\n"
            "Make the constructor accept name and store it using this.\n"
            "Create an object with the name Sara and print the name."
        ),
        "starter_code": "// Create your Student class\n",
        "tests": [
            ("", "Sara", False),
        ],
    },

    "Simple Calculator": {
        "title": "Build a Mini Calculator",
        "description": "Combine the previous concepts to build a calculator.",
        "instructions": (
            "Create a function named add that accepts two numbers "
            "and returns their sum.\n"
            "Call it using 15 and 25, then print the result."
        ),
        "starter_code": "// Create your calculator function\n",
        "tests": [
            ("", "40", False),
        ],
    },
}

@app.on_event("startup")
def initialize_javascript_exercises():
    db = SessionLocal()

    added_count = 0
    skipped_count = 0

    try:
        javascript_course = db.query(CourseModel).filter(
            CourseModel.title == "JavaScript Essentials"
        ).first()

        if javascript_course is None:
            print("[CodeQuest] JavaScript course not found.")
            return

        lessons = (
            db.query(LessonModel)
            .join(
                ModuleModel,
                LessonModel.module_id == ModuleModel.id
            )
            .filter(
                ModuleModel.course_id == javascript_course.id
            )
            .all()
        )

        for lesson in lessons:
            exercise_data = JAVASCRIPT_PRACTICAL_EXERCISES.get(
                lesson.title
            )

            if exercise_data is None:
                continue

            existing_exercise = db.query(ExerciseModel).filter(
                ExerciseModel.lesson_id == lesson.id
            ).first()

            if existing_exercise is not None:
                skipped_count += 1
                continue

            exercise = ExerciseModel(
                lesson_id=lesson.id,
                title=exercise_data["title"],
                description=exercise_data["description"],
                instructions=exercise_data["instructions"],
                starter_code=exercise_data["starter_code"],
                language="javascript",
                difficulty="beginner",
                xp_reward=10,
                order_index=1,
            )

            db.add(exercise)
            db.flush()

            for test in exercise_data["tests"]:
                input_data, expected_output, is_hidden = test

                db.add(
                    TestCaseModel(
                        exercise_id=exercise.id,
                        input_data=input_data,
                        expected_output=expected_output,
                        is_hidden=is_hidden,
                    )
                )

            added_count += 1

        db.commit()

        print(
            "[CodeQuest] JavaScript exercises initialized. "
            f"Added: {added_count}, Skipped: {skipped_count}"
        )

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()
        
@app.on_event("startup")
def initialize_java_exercises():
    db = SessionLocal()


@app.on_event("startup")
def initialize_javascript_content():
    db = SessionLocal()

    javascript_modules = [
        {
            "title": "Introduction to JavaScript",
            "description": "Learn what JavaScript is and how to write your first program.",
            "lessons": [
                ("What is JavaScript?", "Introduction to JavaScript and its uses."),
                ("JavaScript Development Environment", "Learn where and how JavaScript code runs."),
                ("Your First JavaScript Program", "Write your first JavaScript program using console.log()."),
            ],
        },
        {
            "title": "Variables and Data Types",
            "description": "Store values and understand JavaScript data types.",
            "lessons": [
                ("Variables with let and const", "Declare variables using let and const."),
                ("Primitive Data Types", "Understand string, number, boolean, null and undefined."),
                ("Type Conversion", "Convert values between JavaScript data types."),
            ],
        },
        {

            "title": "Operators and Input Output",
            "description": "Use operators and display program results.",
            "lessons": [
                ("Arithmetic Operators", "Perform mathematical operations."),
                ("Comparison and Logical Operators", "Compare values and combine conditions."),
                ("Output and Basic Input", "Display output and understand basic input methods."),
            ],
        },
        {
            "title": "Conditional Statements",
            "description": "Make decisions using conditional statements.",
            "lessons": [
                ("The if Statement", "Execute code when a condition is true."),
                ("if, else and else if", "Handle multiple possible conditions."),
                ("The switch Statement", "Choose between multiple execution paths."),
            ],
        },
        {
            "title": "Loops",
            "description": "Repeat instructions using JavaScript loops.",
            "lessons": [
                ("The for Loop", "Repeat instructions using a for loop."),
                ("The while Loop", "Repeat instructions while a condition is true."),
                ("break and continue", "Control loop execution."),
            ],
        },
        {
            "title": "Arrays and Objects",
            "description": "Organize collections of values and related information.",
            "lessons": [
                ("Creating and Accessing Arrays", "Create arrays and access their elements."),
                ("Array Methods", "Use push, pop, includes and other array methods."),
                ("JavaScript Objects", "Create objects with properties and values."),
            ],
        },
        {
            "title": "Functions",
            "description": "Create reusable blocks of JavaScript code.",
            "lessons": [
                ("Creating Functions", "Declare and call functions."),
                ("Parameters and Return Values", "Pass values into functions and return results."),
                ("Arrow Functions and Scope", "Use arrow functions and understand variable scope."),
            ],
        },
        {
            "title": "Object-Oriented Programming",
            "description": "Learn object-oriented programming concepts in JavaScript.",
            "lessons": [
                ("Classes and Objects", "Create classes and objects."),
                ("Constructors and Methods", "Initialize objects and define their behavior."),
                ("Inheritance", "Extend classes and reuse functionality."),
            ],
        },
        {
            "title": "Mini Projects",
            "description": "Apply JavaScript concepts in practical projects.",
            "lessons": [
                ("Simple Calculator", "Build a calculator using functions and operators."),
                ("Student Grade Manager", "Store student information and calculate grades."),
                ("To-Do List Project", "Build a simple task management program."),
            ],
        },
    ]

    added_modules = 0
    added_lessons = 0

    try:
        javascript_course = db.query(CourseModel).filter(
            CourseModel.title == "JavaScript Essentials"
        ).first()

        if javascript_course is None:
            print("[CodeQuest] JavaScript course not found.")
            return

        for module_index, module_data in enumerate(
            javascript_modules, start=1
        ):
            module = db.query(ModuleModel).filter(
                ModuleModel.course_id == javascript_course.id,
                ModuleModel.order_index == module_index,
            ).first()

            if module is None:
                module = ModuleModel(
                    course_id=javascript_course.id,
                    title=module_data["title"],
                    description=module_data["description"],
                    order_index=module_index,
                )
                db.add(module)
                db.flush()
                added_modules += 1

            for lesson_index, lesson_data in enumerate(
                module_data["lessons"], start=1
            ):
                existing_lesson = db.query(LessonModel).filter(
                    LessonModel.module_id == module.id,
                    LessonModel.order_index == lesson_index,
                ).first()

                if existing_lesson is None:
                    db.add(
                        LessonModel(
                            module_id=module.id,
                            title=lesson_data[0],
                            description=lesson_data[1],
                            lesson_type="concept",
                            duration_minutes=5,
                            order_index=lesson_index,
                        )
                    )
                    added_lessons += 1

        db.commit()

        print(
            "[CodeQuest] JavaScript content initialized. "
            f"Modules added: {added_modules}, "
            f"lessons added: {added_lessons}"
        )

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()

# =================================
# 15. Get all courses
# =================================

@app.get("/courses")
def get_courses(db: Session = Depends(get_db)):
    return db.query(CourseModel).all()


# =================================
# 16. Get modules of a course
# =================================

@app.get("/courses/{course_id}/modules")
def get_course_modules(
    course_id: int,
    db: Session = Depends(get_db),
):
    course = db.query(CourseModel).filter(
        CourseModel.id == course_id
    ).first()

    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    return db.query(ModuleModel).filter(
        ModuleModel.course_id == course_id
    ).order_by(ModuleModel.order_index).all()




# ==========================================

# ==========================================
# Expert Content Management API
# ==========================================

@app.post("/admin/courses")
def admin_create_course(
    payload: CourseCreate,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_expert),
):
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Course title is required")

    course = CourseModel(
        title=title,
        description=payload.description.strip(),
    )
    db.add(course)
    db.commit()
    db.refresh(course)

    return {
        "message": "Course created successfully",
        "course_id": course.id,
        "title": course.title,
    }


@app.put("/admin/courses/{course_id}")
def admin_update_course(
    course_id: int,
    payload: CourseUpdate,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_expert),
):
    course = db.query(CourseModel).filter(
        CourseModel.id == course_id
    ).first()

    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    updates = payload.dict(exclude_unset=True)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided")

    for field, value in updates.items():
        if field == "title":
            if value is None or not value.strip():
                raise HTTPException(status_code=400, detail="Course title cannot be empty")
            value = value.strip()
        elif field == "description" and value is not None:
            value = value.strip()

        setattr(course, field, value)

    db.commit()
    db.refresh(course)

    return {
        "message": "Course updated successfully",
        "course_id": course.id,
        "title": course.title,
    }


@app.delete("/admin/courses/{course_id}")
def admin_delete_course(
    course_id: int,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_expert),
):
    course = db.query(CourseModel).filter(
        CourseModel.id == course_id
    ).first()

    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    has_modules = db.query(ModuleModel).filter(
        ModuleModel.course_id == course_id
    ).first()

    if has_modules:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a course that contains modules. Remove its modules first.",
        )

    db.delete(course)
    db.commit()

    return {"message": "Course deleted successfully"}


@app.post("/admin/courses/{course_id}/modules")
def admin_create_module(
    course_id: int,
    payload: ModuleCreate,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_expert),
):
    course = db.query(CourseModel).filter(
        CourseModel.id == course_id
    ).first()

    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    if payload.course_id != course_id:
        raise HTTPException(status_code=400, detail="Course ID mismatch")

    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Module title is required")

    module = ModuleModel(
        course_id=course_id,
        title=title,
        description=payload.description.strip(),
        order_index=payload.order_index,
    )
    db.add(module)
    db.commit()
    db.refresh(module)

    return {
        "message": "Module created successfully",
        "module_id": module.id,
        "title": module.title,
    }


@app.put("/admin/modules/{module_id}")
def admin_update_module(
    module_id: int,
    payload: ModuleUpdate,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_expert),
):
    module = db.query(ModuleModel).filter(
        ModuleModel.id == module_id
    ).first()

    if module is None:
        raise HTTPException(status_code=404, detail="Module not found")

    updates = payload.dict(exclude_unset=True)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided")

    for field, value in updates.items():
        if field == "title":
            if value is None or not value.strip():
                raise HTTPException(status_code=400, detail="Module title cannot be empty")
            value = value.strip()
        elif field == "description" and value is not None:
            value = value.strip()

        setattr(module, field, value)

    db.commit()
    db.refresh(module)

    return {
        "message": "Module updated successfully",
        "module_id": module.id,
        "title": module.title,
    }


@app.delete("/admin/modules/{module_id}")
def admin_delete_module(
    module_id: int,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_expert),
):
    module = db.query(ModuleModel).filter(
        ModuleModel.id == module_id
    ).first()

    if module is None:
        raise HTTPException(status_code=404, detail="Module not found")

    has_lessons = db.query(LessonModel).filter(
        LessonModel.module_id == module_id
    ).first()

    if has_lessons:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a module that contains lessons. Remove its lessons first.",
        )

    db.delete(module)
    db.commit()

    return {"message": "Module deleted successfully"}


@app.post("/admin/modules/{module_id}/lessons")
def admin_create_lesson(
    module_id: int,
    payload: LessonCreate,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_expert),
):
    module = db.query(ModuleModel).filter(
        ModuleModel.id == module_id
    ).first()

    if module is None:
        raise HTTPException(status_code=404, detail="Module not found")

    if payload.module_id != module_id:
        raise HTTPException(status_code=400, detail="Module ID mismatch")

    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Lesson title is required")

    lesson = LessonModel(
        module_id=module_id,
        title=title,
        description=payload.description.strip(),
        lesson_type=payload.lesson_type,
        duration_minutes=payload.duration_minutes,
        order_index=payload.order_index,
    )
    db.add(lesson)
    db.commit()
    db.refresh(lesson)

    return {
        "message": "Lesson created successfully",
        "lesson_id": lesson.id,
        "title": lesson.title,
    }


@app.put("/admin/lessons/{lesson_id}")
def admin_update_lesson(
    lesson_id: int,
    payload: LessonUpdate,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_expert),
):
    lesson = db.query(LessonModel).filter(
        LessonModel.id == lesson_id
    ).first()

    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")

    updates = payload.dict(exclude_unset=True)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided")

    for field, value in updates.items():
        if field == "title":
            if value is None or not value.strip():
                raise HTTPException(status_code=400, detail="Lesson title cannot be empty")
            value = value.strip()
        elif field == "description" and value is not None:
            value = value.strip()

        setattr(lesson, field, value)

    db.commit()
    db.refresh(lesson)

    return {
        "message": "Lesson updated successfully",
        "lesson_id": lesson.id,
        "title": lesson.title,
    }


@app.delete("/admin/lessons/{lesson_id}")
def admin_delete_lesson(
    lesson_id: int,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_expert),
):
    lesson = db.query(LessonModel).filter(
        LessonModel.id == lesson_id
    ).first()

    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")

    has_progress = db.query(LessonProgressModel).filter(
        LessonProgressModel.lesson_id == lesson_id
    ).first()

    has_xp = db.query(XPTransactionModel).filter(
        XPTransactionModel.lesson_id == lesson_id
    ).first()

    has_exercises = db.query(ExerciseModel).filter(
        ExerciseModel.lesson_id == lesson_id
    ).first()

    if has_progress or has_xp or has_exercises:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete this lesson because it has student progress, XP records, or exercises.",
        )

    db.delete(lesson)
    db.commit()

    return {"message": "Lesson deleted successfully"}

# Python lesson curriculum content (25 lessons)
# Stored in code, not in SQLite, so existing
# student progress and XP data remain untouched.
# ==========================================

def _q(question, options, answer, explanation):
    return {
        "question": question,
        "options": options,
        "answer": answer,
        "explanation": explanation,
    }

PYTHON_LESSON_CONTENT = {
    "What is Programming?": {
        "content": "Programming is the process of writing clear and organized instructions for a computer to perform a task. A computer follows instructions exactly as they are written, so the order and correctness of the steps are important. We usually start by understanding the problem, identifying the inputs, processing, and outputs, then writing and testing the solution.",
        "examples": [
            {"title": "Steps to Complete a Task", "code": "# Algorithm for preparing a glass of water\n1. Get a glass\n2. Add water\n3. Serve the glass", "output": "The steps are executed in order", "explanation": "This is an algorithm written in human language. It describes the order of the instructions."},
            {"title": "Representing Steps in Python", "code": 'print("Get a glass")\nprint("Add water")\nprint("Serve the glass")', "output": "Get a glass\nAdd water\nServe the glass", "explanation": "Each print() call displays a line, and the program executes statements from top to bottom."}
        ],
        "questions": [
            _q("What is programming?", ["Writing instructions for a computer", "Changing only the screen appearance", "Turning on a device without instructions"], 0, "A program is a set of instructions executed by a computer."),
            _q("What is a suitable first step before writing a solution?", ["Writing random commands", "Understanding the problem", "Deleting the inputs"], 1, "Understanding the problem helps us identify what is required."),
            _q("Why is the order of instructions important?", ["Because the computer executes them in order", "To increase the file size", "To change the programming language"], 0, "The order of steps can change the result."),
            _q("What do we call an ordered set of solution steps?", ["Variable", "Algorithm", "Window"], 1, "An algorithm is an ordered description of how to solve a problem.")
        ]
    },
    "Introduction to Python": {
        "content": "Python is a high-level programming language known for its relatively readable syntax. It is used in web applications, data analysis, artificial intelligence, automation, and education. We write commands in a file with the .py extension or execute them in an interactive environment. Python uses indentation to organize code blocks.",
        "examples": [
            {"title": "Your First Command", "code": 'print("Hello, Python")', "output": "Hello, Python", "explanation": "The print() function displays the value inside the parentheses."},
            {"title": "A Comment in Code", "code": '# This is a comment that Python does not execute\nprint("We have started learning")', "output": "We have started learning", "explanation": "A comment starts with the # symbol and is used to explain the code."}
        ],
        "questions": [
            _q("What is the usual file extension for Python?", [".html", ".py", ".jpg"], 1, "Python files use the .py extension."),
            _q("What is the purpose of print()?", ["Displaying a value", "Creating a folder", "Disabling the keyboard"], 0, "The print() function displays text or a value."),
            _q("Which symbol starts a comment in Python?", ["//", "<!--", "#"], 2, "A single-line comment starts with the # symbol."),
            _q("Where is Python used?", ["Data analysis, web development, and artificial intelligence", "Circuit design only", "Document formatting only"], 0, "Python has many applications in different fields.")
        ]
    },
    "Your First Python Program": {
        "content": "You can write your first program using print(). Place the text between quotation marks and put the value inside the function's parentheses. Python executes statements in order. Make sure to close parentheses and quotation marks, because forgetting them often causes a SyntaxError.",
        "examples": [
            {"title": "Printing a Message", "code": 'print("Hello, CodeQuest!")', "output": "Hello, CodeQuest!", "explanation": "The text is displayed exactly as it appears between the quotation marks."},
            {"title": "Printing Multiple Lines", "code": 'print("My name is Sarah")\nprint("I am learning Python")', "output": "My name is Sarah\nI am learning Python", "explanation": "Each print() call usually starts a new line."}
        ],
        "questions": [
            _q("Which command prints the word Hi?", ['print("Hi")', "show Hi", "echo = Hi"], 0, "The correct syntax is calling print() with a string."),
            _q("Where should the text to be printed be placed?", ["Between quotation marks", "After removing the parentheses", "Inside #"], 0, "Strings are placed between single or double quotation marks."),
            _q("How many lines are produced by two consecutive print() calls?", ["Always one line", "Two lines", "Nothing"], 1, "Each call prints on a separate line by default."),
            _q("What error may occur if a closing parenthesis is missing?", ["SyntaxError", "Increasing XP", "Changing the file type"], 0, "Incomplete syntax causes a syntax error.")
        ]
    },
    "What are Variables?": {
        "content": "A variable is a name used to refer to a stored value while a program is running. We create it by writing the name, followed by =, and then the value. We do not need to specify the variable type in advance; Python determines the type from the assigned value. Choose clear names and start each name with a letter or an underscore.",
        "examples": [
            {"title": "Storing a Name", "code": 'student_name = "Lina"\nprint(student_name)', "output": "Lina", "explanation": "We stored a string in a variable and then printed its value."},
            {"title": "Updating a Value", "code": "score = 5\nscore = score + 2\nprint(score)", "output": "7", "explanation": "A new value can be assigned to a variable based on its previous value."}
        ],
        "questions": [
            _q("What is the purpose of a variable?", ["Storing a value under a name", "Closing the program", "Drawing an image"], 0, "A variable associates a name with a value."),
            _q("What is the assignment operator in Python?", ["==", "=", "!="], 1, "The = symbol assigns a value to a variable."),
            _q("Which of the following is a valid variable name?", ["2name", "student_name", "student-name"], 1, "A variable name cannot start with a number or contain a regular hyphen."),
            _q("What is the result of x=3, then x=x+4, then print(x)?", ["3", "4", "7"], 2, "The new value is equal to 3 + 4.")
        ]
    },
    "Numbers and Strings": {
        "content": "Python works with numeric types such as int for integers and float for decimal numbers, and str for text. Arithmetic operations can be performed on numbers, while + can be used to concatenate strings. You cannot directly add a string and a number without converting the appropriate type.",
        "examples": [
            {"title": "Numbers", "code": "age = 20\nprice = 3.5\nprint(age + 2)\nprint(price * 2)", "output": "22\n7.0", "explanation": "int represents an integer, while float represents a decimal number."},
            {"title": "Concatenating Strings", "code": 'first = "Code"\nsecond = "Quest"\nprint(first + second)', "output": "CodeQuest", "explanation": "The + operator joins strings when both operands are of type str."}
        ],
        "questions": [
            _q("Which type represents an integer?", ["str", "int", "bool"], 1, "int is used for integers."),
            _q("What is the type of the value 4.25?", ["float", "int", "str"], 0, "A decimal part makes the value a float."),
            _q('What is the result of "Py" + "thon"?', ["Py thon", "Python", "Error"], 1, "The two strings are joined without adding a space."),
            _q("What happens when a string and a number are added without conversion?", ["A TypeError usually occurs", "They are always converted to a string", "The value is deleted"], 0, "The types must be compatible before addition.")
        ]
    },
    "Booleans and Type Conversion": {
        "content": "The bool type has only two values: True and False. It is used in conditions and comparison results. We can convert values using functions such as int(), float(), str(), and bool(). Remember that input() always returns a string, so we convert the input to a number when necessary.",
        "examples": [
            {"title": "A Boolean Value", "code": "is_active = True\nprint(is_active)", "output": "True", "explanation": "A Boolean value is written as True or False, with an uppercase first letter."},
            {"title": "Converting a String to a Number", "code": 'age_text = "18"\nage = int(age_text)\nprint(age + 2)', "output": "20", "explanation": "We used int() to convert the numeric string into an integer."}
        ],
        "questions": [
            _q("What are the two possible values of the bool type?", ["Yes and No", "True and False", "Only 1 and 2"], 1, "The bool type has two Boolean values."),
            _q("What is the result of int('12')?", ["'12'", "12", "12.0"], 1, "int() converts a numeric string into an integer."),
            _q("What is the return type of input()?", ["int", "float", "str"], 2, "input() returns a string, even when the user enters numbers."),
            _q("What is the result of 5 > 3?", ["True", "False", "5"], 0, "Five is greater than three.")
        ]
    },
    "Output with print()": {
        "content": "The print() function is used to display text, variables, or the result of an operation. You can pass multiple values, and the function separates them with a space by default. You can change the separator using sep and change the ending of the output using end.",
        "examples": [
            {"title": "Printing Multiple Values", "code": 'name = "Mona"\nscore = 90\nprint(name, score)', "output": "Mona 90", "explanation": "print() separates multiple values with a space by default."},
            {"title": "Changing the Separator", "code": 'print("A", "B", "C", sep="-")', "output": "A-B-C", "explanation": "sep specifies the text used to separate the items."}
        ],
        "questions": [
            _q("Which function is used to display output?", ["input()", "print()", "type()"], 1, "print() displays data."),
            _q("What is the default separator between multiple print() values?", ["A space", "A hyphen", "Nothing, always"], 0, "The default separator is a space."),
            _q('What is the result of print("A", "B", sep=":")?', ["A B", "A:B", "AB"], 1, "sep specifies the separator between values."),
            _q("What is the purpose of end in print()?", ["Specifying the end of the output", "Converting text to a number", "Reading user input"], 0, "end specifies what is added at the end of a print() call.")
        ]
    },
    "User Input": {
        "content": "The input() function reads a value entered by the user and returns it as a string. You can place a prompt message inside the parentheses. If you want to perform calculations on numeric input, convert it using int() or float(), then display the result using print().",
        "examples": [
            {"title": "Reading a Name", "code": 'name = input("Enter your name: ")\nprint("Hello", name)', "output": "When the user enters Lina:\nHello Lina", "explanation": "The prompt is displayed, and the user's response is stored in name."},
            {"title": "Reading a Number", "code": 'age = int(input("Age: "))\nprint(age + 1)', "output": "When the user enters 17:\n18", "explanation": "We converted the text input to an integer before performing addition."}
        ],
        "questions": [
            _q("What is the purpose of input()?", ["Reading user input", "Printing the result", "Creating a list"], 0, "input() waits for the user to enter a value."),
            _q("What type of value does input() return?", ["str", "Always int", "bool"], 0, "The returned value is a string."),
            _q("How do we convert input to an integer?", ["str()", "int()", "print()"], 1, "int() converts a suitable value into an integer."),
            _q("Why do we use int(input()) when asking for age?", ["To perform numerical operations", "To color the text", "To prevent input"], 0, "Conversion allows us to perform calculations using the value as a number.")
        ]
    },
    "Python Operators": {
        "content": "Python provides arithmetic operators such as +, -, *, /, //, %, and **; comparison operators such as ==, !=, >, and <; and logical operators such as and, or, and not. Comparisons return a Boolean value that can be used in conditions.",
        "examples": [
            {"title": "Arithmetic Operations", "code": "print(10 // 3)\nprint(10 % 3)\nprint(2 ** 3)", "output": "3\n1\n8", "explanation": "// performs floor division, % returns the remainder, and ** performs exponentiation."},
            {"title": "Comparison and Logic", "code": "age = 20\nprint(age >= 18 and age < 60)", "output": "True", "explanation": "and returns True when both conditions are true."}
        ],
        "questions": [
            _q("What is the purpose of %?", ["Remainder of division", "Exponentiation", "Regular division"], 0, "% returns the remainder of a division operation."),
            _q("What is the result of 2 ** 4?", ["6", "8", "16"], 2, "2 to the power of 4 equals 16."),
            _q("Which operator means equality comparison?", ["=", "==", "=>"], 1, "== is used for comparison, while = is used for assignment."),
            _q("When is A and B True?", ["When both are True", "When only one is True", "Always"], 0, "and requires both conditions to be true.")
        ]
    },
    "The if Statement": {
        "content": "The if statement allows a block of commands to execute when a condition is satisfied. Write the condition after if, followed by a colon. The statements inside the block must be indented. If the condition evaluates to True, the block executes; if it evaluates to False, the block is skipped.",
        "examples": [
            {"title": "Checking Age", "code": "age = 19\nif age >= 18:\n    print('Adult')", "output": "Adult", "explanation": "The condition is True, so the indented statement under if is executed."},
            {"title": "A Condition That Is Not Met", "code": "temperature = 15\nif temperature > 30:\n    print('Hot')\nprint('Done')", "output": "Done", "explanation": "print('Hot') was not executed because the condition was False."}
        ],
        "questions": [
            _q("When does an if block execute?", ["When the condition is True", "Always", "Only when the condition is a string"], 0, "The block executes when the condition is satisfied."),
            _q("Which symbol comes after an if condition?", ["Comma", "Colon :", "Question mark"], 1, "We place a colon : after the condition."),
            _q("Why do we use indentation?", ["To define the block of statements", "To change the variable type", "To print text"], 0, "Indentation identifies the statements belonging to the condition."),
            _q("If the condition is False and there is no else, what happens?", ["The block is skipped", "The block executes 2 times", "The computer stops"], 0, "The statements inside if do not execute when the condition is not satisfied.")
        ]
    },
    "if, elif and else": {
        "content": "We use elif to check an additional condition if the previous condition is not satisfied, and else for the remaining case. Conditions are checked in order, and only the first block whose condition is True is executed. The program then skips the rest of the conditional chain.",
        "examples": [
            {"title": "Assigning a Grade", "code": "score = 85\nif score >= 90:\n    print('A')\nelif score >= 80:\n    print('B')\nelse:\n    print('C')", "output": "B", "explanation": "The first condition is False and the second is True, so B is displayed."},
            {"title": "Checking a Number", "code": "number = 0\nif number > 0:\n    print('Positive')\nelif number < 0:\n    print('Negative')\nelse:\n    print('Zero')", "output": "Zero", "explanation": "Neither of the previous conditions was satisfied, so else was executed."}
        ],
        "questions": [
            _q("When do we use elif?", ["To check an alternative condition", "To print a variable", "To create a loop"], 0, "elif adds another condition after if."),
            _q("When does else execute?", ["When none of the previous conditions is satisfied", "Always before if", "When the first condition is True"], 0, "else represents the default case."),
            _q("Can more than one branch execute in a single if/elif/else chain?", ["Yes, all branches", "Only the first satisfied branch executes", "No branch executes"], 1, "The chain stops after the first condition that evaluates to True."),
            _q("What is the result for score=70 with if score>=90 / elif score>=80 / else?", ["A", "B", "Nothing"], 2, "Neither condition is satisfied, so else executes.")
        ]
    },
    "Conditional Practice": {
        "content": "In practical exercises, we first identify the possible cases, then write conditions that cover them without unintended overlap. Use suitable comparison operators and pay attention to range boundaries such as >= and <. Test the program with values representing every case, including boundary values.",
        "examples": [
            {"title": "Checking Even or Odd", "code": "n = 8\nif n % 2 == 0:\n    print('Even')\nelse:\n    print('Odd')", "output": "Even", "explanation": "An even number has a remainder of zero when divided by 2."},
            {"title": "Delivery Fees", "code": "total = 45\nif total >= 50:\n    print('Free delivery')\nelse:\n    print('Delivery fee applies')", "output": "Delivery fee applies", "explanation": "The value 45 is less than 50, so else is executed."}
        ],
        "questions": [
            _q("How can we determine whether a number is even?", ["n % 2 == 0", "n / 2 == 1", "n + 2 == 0"], 0, "An even number has no remainder when divided by 2."),
            _q("Why do we test boundary values?", ["To make sure condition boundaries are correct", "To change the application language", "To increase the number of variables"], 0, "Boundary values can reveal comparison errors."),
            _q("Which operator includes the boundary value itself?", [">", ">=", "!="], 1, ">= means greater than or equal to."),
            _q("What is the best way to test a condition with multiple cases?", ["Test only one case", "Test a value for each case and its boundaries", "Do not run the program"], 1, "Testing different cases makes the solution more reliable.")
        ]
    },
    "The for Loop": {
        "content": "A for loop repeats a block of statements over the elements of a sequence such as a list, string, or range. The range function is often used to generate a sequence of numbers. You can specify a start, stop, and step value. The stop value is not included.",
        "examples": [
            {"title": "Looping from 0 to 4", "code": "for i in range(5):\n    print(i)", "output": "0\n1\n2\n3\n4", "explanation": "range(5) generates numbers from 0 to 4."},
            {"title": "Iterating Through a List", "code": 'names = ["Ali", "Mona"]\nfor name in names:\n    print(name)', "output": "Ali\nMona", "explanation": "name takes each item from the list in sequence."}
        ],
        "questions": [
            _q("What is the purpose of for?", ["Iterating over elements", "Defining a class", "Converting a number"], 0, "for executes the block for each element in the sequence."),
            _q("Which numbers are generated by range(3)?", ["1,2,3", "0,1,2", "0,1,2,3"], 1, "The stop value is not included."),
            _q("How many times does for i in range(4) execute?", ["3", "4", "5"], 1, "range(4) generates four numbers."),
            _q("Can we iterate over the characters of a string using for?", ["Yes", "No", "Only if it is a number"], 0, "A string is a sequence of characters.")
        ]
    },
    "The while Loop": {
        "content": "A while loop repeats a block of statements as long as its condition is True. You should update the variables that affect the condition so the loop can stop when needed. If the condition remains True without an appropriate update, the loop may continue indefinitely. Therefore, plan the stopping condition carefully.",
        "examples": [
            {"title": "Countdown", "code": "count = 3\nwhile count > 0:\n    print(count)\n    count -= 1", "output": "3\n2\n1", "explanation": "We decrease count in each iteration until the condition becomes False."},
            {"title": "Adding Until a Limit", "code": "total = 0\nnumber = 1\nwhile total < 6:\n    total += number\n    number += 1\nprint(total)", "output": "6", "explanation": "The loop continues until the total reaches 6 or more."}
        ],
        "questions": [
            _q("When does a while loop continue?", ["As long as the condition is True", "Always once", "When the condition is False"], 0, "while repeats as long as its condition is satisfied."),
            _q("What is a common cause of an infinite loop?", ["Not updating the value that affects the condition", "Using print()", "Having a string variable"], 0, "If the condition does not change, it may remain True."),
            _q("What is the value of count after count=3 and then count-=1?", ["3", "2", "1"], 1, "The value decreases by one."),
            _q("What happens when the condition becomes False?", ["The loop stops", "The loop starts again", "The variables are deleted"], 0, "A while loop exits when its condition is no longer satisfied.")
        ]
    },
    "break and continue": {
        "content": "The break statement immediately terminates the loop and exits it, while continue skips the remaining statements in the current iteration and moves to the next iteration. We use them to control loop execution. Pay attention to their position so that an important update is not skipped.",
        "examples": [
            {"title": "Stopping the Loop", "code": "for n in range(5):\n    if n == 3:\n        break\n    print(n)", "output": "0\n1\n2", "explanation": "When n becomes 3, the loop terminates."},
            {"title": "Skipping a Number", "code": "for n in range(5):\n    if n == 2:\n        continue\n    print(n)", "output": "0\n1\n3\n4", "explanation": "Printing is skipped in the iteration where n equals 2."}
        ],
        "questions": [
            _q("What is the purpose of break?", ["Terminating the loop", "Skipping only one iteration", "Starting a function"], 0, "break exits the loop immediately."),
            _q("What is the purpose of continue?", ["Terminating the program", "Skipping the remaining statements in the current iteration", "Redefining the loop"], 1, "continue moves to the next iteration."),
            _q("What is the output of for i in range(4) with break when i==1?", ["0", "0 then 1 then 2", "1 then 2 then 3"], 0, "It prints 0 and then stops when i=1 before printing it."),
            _q("Does continue terminate the entire loop?", ["Yes", "No", "Only in while"], 1, "It skips the current iteration without terminating the loop.")
        ]
    },
    "Lists": {
        "content": "A list is an ordered and mutable collection that can contain elements of different types. We write its elements inside [], and access them using an index that starts at zero. We can add an element using append() and modify an element by assigning a new value to its index.",
        "examples": [
            {"title": "Creating and Accessing a List", "code": 'colors = ["red", "blue", "green"]\nprint(colors[0])', "output": "red", "explanation": "Index 0 refers to the first element."},
            {"title": "Adding an Element", "code": "numbers = [2, 4]\nnumbers.append(6)\nprint(numbers)", "output": "[2, 4, 6]", "explanation": "append() adds an element to the end of the list."}
        ],
        "questions": [
            _q("Which brackets are used to create a list?", ["{}", "[]", "()"], 1, "Lists are written inside square brackets."),
            _q("What is the index of the first element?", ["0", "1", "-1 only"], 0, "Indexing starts at zero."),
            _q("What is the purpose of append()?", ["Adding an element to the end", "Deleting the list", "Formatting text"], 0, "append() adds a new element."),
            _q("Can list elements be modified?", ["Yes", "No", "Only numbers"], 0, "A list is mutable.")
        ]
    },
    "Tuples and Sets": {
        "content": "A tuple is an ordered collection that cannot be modified after creation. It is usually written inside (). A set is an unordered collection of unique elements. It is written inside {} or created using set(). Sets are useful for removing duplicates and checking whether an element exists.",
        "examples": [
            {"title": "Tuple", "code": 'point = (3, 5)\nprint(point[0])', "output": "3", "explanation": "You can access tuple elements using an index, but you cannot modify them."},
            {"title": "Set and Removing Duplicates", "code": "values = {1, 2, 2, 3}\nprint(len(values))", "output": "3", "explanation": "A set keeps only unique values."}
        ],
        "questions": [
            _q("Which data structure cannot be modified?", ["list", "tuple", "dict"], 1, "A tuple cannot be changed after it is created."),
            _q("What is the main feature of a set?", ["It always allows duplicates", "It keeps unique elements", "It guarantees index-based order"], 1, "A set removes duplicate values."),
            _q("What is the result of len({1,1,2})?", ["3", "2", "1"], 1, "The set contains two unique elements."),
            _q("Can we access set elements using an index?", ["Yes", "No", "Only the first element"], 1, "A set does not use positional indexing.")
        ]
    },
    "Dictionaries": {
        "content": "A dictionary stores data as key-value pairs. We write a dictionary inside {}, and use a key to access or modify its value. Keys must be unique and usable as dictionary keys. You can add a new pair by assigning a value to a new key.",
        "examples": [
            {"title": "Reading a Value", "code": 'student = {"name": "Lina", "grade": 90}\nprint(student["name"])', "output": "Lina", "explanation": "We used the name key to access its corresponding value."},
            {"title": "Adding a Value", "code": 'student = {"name": "Lina"}\nstudent["age"] = 19\nprint(student["age"])', "output": "19", "explanation": "Assigning a value to a new key adds a pair to the dictionary."}
        ],
        "questions": [
            _q("How does a dictionary store data?", ["Key-value pairs", "Indexes only", "Rows and columns only"], 0, "Each dictionary item is a key:value pair."),
            _q("How do we access the value of name?", ['data["name"]', "data.name()", "data(0)"], 0, "We use the key inside square brackets."),
            _q("Can we add a new key?", ["Yes", "No", "Only when creating the dictionary"], 0, "We can assign a value to a new key."),
            _q("Can dictionary keys be repeated?", ["Each key is unique", "They must be repeated", "There are no keys"], 0, "Each key refers to one value.")
        ]
    },
    "Creating Functions": {
        "content": "A function is a named block of statements used to organize code and reuse it. We define a function using def, followed by the function name, parentheses, and a colon. The function body does not execute when it is defined; it executes when the function is called by writing its name with parentheses.",
        "examples": [
            {"title": "A Greeting Function", "code": 'def greet():\n    print("Welcome")\n\ngreet()', "output": "Welcome", "explanation": "We defined the function and then called it to execute its body."},
            {"title": "Calling a Function More Than Once", "code": "def line():\n    print('---')\n\nline()\nline()", "output": "---\n---", "explanation": "You can reuse a function without writing its body again."}
        ],
        "questions": [
            _q("Which keyword is used to define a function?", ["class", "def", "loop"], 1, "Function definitions start with the def keyword."),
            _q("When does a function body execute?", ["When the function is called", "Only when the file is opened", "Before it is defined"], 0, "Defining a function does not call it."),
            _q("What is the benefit of functions?", ["Reusing and organizing code", "Increasing errors", "Deleting variables"], 0, "Functions reduce repetition and organize solutions."),
            _q("How do we call greet?", ["call greet", "greet()", "def greet"], 1, "We write the function name followed by parentheses.")
        ]
    },
    "Parameters and Return": {
        "content": "Parameters are names that receive values when a function is called, while the values passed to the function are called arguments. The return statement sends a result back to the caller and ends the function's execution. If you do not write an explicit return statement, the function returns None.",
        "examples": [
            {"title": "A Function with a Parameter", "code": "def greet(name):\n    print('Hello', name)\n\ngreet('Mona')", "output": "Hello Mona", "explanation": "name receives the value Mona when the function is called."},
            {"title": "Returning a Result", "code": "def add(a, b):\n    return a + b\n\nresult = add(3, 4)\nprint(result)", "output": "7", "explanation": "return sends the sum of the two parameters to be stored in result."}
        ],
        "questions": [
            _q("What is a parameter?", ["A name that receives a value inside a function", "A printed result", "A file type"], 0, "A parameter is a variable in the function definition."),
            _q("What is the purpose of return?", ["Returning a value and ending the function", "Repeating a loop", "Reading the keyboard"], 0, "return sends a result back to the caller."),
            _q("What does a function return if it has no explicit return statement?", ["None", "Always True", "Always 0"], 0, "The default returned value is None."),
            _q("In add(2,5), what are the arguments?", ["a and b", "2 and 5", "add only"], 1, "2 and 5 are the values passed to the function.")
        ]
    },
    "Functions Practice": {
        "content": "When creating a function to solve a problem, identify the inputs it needs and the result it should return. Give each function a clear responsibility and test it with normal and boundary values. Separate calculations from printing when it is better to return the result using return.",
        "examples": [
            {"title": "Calculating Rectangle Area", "code": "def area(width, height):\n    return width * height\n\nprint(area(4, 3))", "output": "12", "explanation": "The function receives the width and height and returns the area."},
            {"title": "Finding the Larger Number", "code": "def larger(a, b):\n    if a > b:\n        return a\n    return b\n\nprint(larger(8, 5))", "output": "8", "explanation": "The function returns the larger value between the two inputs."}
        ],
        "questions": [
            _q("What should be identified before writing a function?", ["Its inputs and outputs", "The screen color", "The operating system name"], 0, "Identifying inputs and results clarifies the function's responsibility."),
            _q("What is the benefit of testing boundary values?", ["Finding special cases", "Changing the function type", "Removing return"], 0, "Boundary values can reveal logic errors."),
            _q("Which statement is best for returning a calculation result?", ["return", "input", "break"], 0, "return sends the result back to the place where the function was called."),
            _q("Why do we divide a solution into small functions?", ["To make it easier to understand and test", "Only to make the code longer", "To prevent reuse"], 0, "Each small function is easier to understand and maintain.")
        ]
    },
    "Classes and Objects": {
        "content": "Object-oriented programming organizes a program around objects. A class is a blueprint that defines data and behavior, while an object is an instance created from that blueprint. We use class to define a class, then create an object by calling the class name with parentheses.",
        "examples": [
            {"title": "Creating a Class", "code": "class Dog:\n    pass\n\npet = Dog()\nprint(type(pet).__name__)", "output": "Dog", "explanation": "We created an object named pet from the Dog class."},
            {"title": "Two Objects from One Class", "code": "class Lamp:\n    pass\n\nlamp1 = Lamp()\nlamp2 = Lamp()\nprint(lamp1 is lamp2)", "output": "False", "explanation": "Each call creates an independent object."}
        ],
        "questions": [
            _q("What is a class?", ["A blueprint for creating objects", "A Boolean value", "A loop"], 0, "A class defines the structure and behavior of objects."),
            _q("What is an object?", ["An instance of a class", "A file name", "A comment"], 0, "An object is an instance of a class."),
            _q("Which keyword defines a class?", ["def", "class", "object"], 1, "We use class to define a class."),
            _q("How do we create an object from Dog?", ["Dog()", "new Dog", "create Dog"], 0, "Calling the class creates an object.")
        ]
    },
    "Attributes and Methods": {
        "content": "Attributes represent data associated with an object, while methods are functions defined inside a class that describe its behavior. We use self to refer to the current object. An object attribute can be defined using self.name, for example, and a method can be called through object.method().",
        "examples": [
            {"title": "An Attribute and a Method", "code": "class Student:\n    def set_name(self, name):\n        self.name = name\n\ns = Student()\ns.set_name('Omar')\nprint(s.name)", "output": "Omar", "explanation": "We stored the name as an attribute inside the object."},
            {"title": "A Method That Displays Data", "code": "class Counter:\n    def show(self):\n        print('Ready')\n\nc = Counter()\nc.show()", "output": "Ready", "explanation": "show is a method associated with an object of the Counter class."}
        ],
        "questions": [
            _q("What is an attribute?", ["Data associated with an object", "A loop", "An exception type"], 0, "An attribute stores the state of an object."),
            _q("What is a method?", ["A function inside a class", "Only a constant value", "A database name"], 0, "A method is a function associated with a class or object."),
            _q("What does self usually refer to?", ["The current object", "Always the parent class", "A Python file"], 0, "self is a reference to the current instance."),
            _q("How do we call show on c?", ["show(c)", "c.show()", "self.show"], 1, "A method is called through the object.")
        ]
    },
    "Constructors and __init__": {
        "content": "The constructor in Python is the special method __init__, which is called automatically when a new object is created. We use it to initialize the object's attributes with initial values. It receives self first, followed by the parameters needed when creating the object.",
        "examples": [
            {"title": "Initializing a Name", "code": "class Student:\n    def __init__(self, name):\n        self.name = name\n\ns = Student('Lana')\nprint(s.name)", "output": "Lana", "explanation": "__init__ was called when the Student object was created."},
            {"title": "More Than One Attribute", "code": "class Book:\n    def __init__(self, title, pages):\n        self.title = title\n        self.pages = pages\n\nb = Book('Python', 120)\nprint(b.pages)", "output": "120", "explanation": "The constructor received two values and stored them in the object's attributes."}
        ],
        "questions": [
            _q("When is __init__ called?", ["When an object is created", "When a file is deleted", "With every print() call"], 0, "It initializes the object when it is created."),
            _q("What is usually the first parameter in __init__?", ["self", "return", "class"], 0, "self refers to the object being initialized."),
            _q("What is the purpose of a constructor?", ["Initializing object attributes", "Running an infinite loop", "Printing all variables"], 0, "We use it to set the object's initial values."),
            _q("In Book('Python',120), what is the value of pages?", ["'Python'", "120", "Always None"], 1, "The second value is assigned to pages.")
        ]
    },
    "Inheritance": {
        "content": "Inheritance allows a new class (child) to use the attributes and methods of an existing class (parent). We write the parent class name in parentheses after the child class name. The child can add new behavior or override an inherited method. Inheritance helps reuse code.",
        "examples": [
            {"title": "A Child Class", "code": "class Animal:\n    def speak(self):\n        print('Sound')\n\nclass Cat(Animal):\n    pass\n\ncat = Cat()\ncat.speak()", "output": "Sound", "explanation": "Cat inherited the speak method from Animal."},
            {"title": "Overriding a Method", "code": "class Animal:\n    def speak(self):\n        print('Sound')\n\nclass Dog(Animal):\n    def speak(self):\n        print('Bark')\n\nDog().speak()", "output": "Bark", "explanation": "Dog provided its own implementation of the speak method."}
        ],
        "questions": [
            _q("What is the benefit of inheritance?", ["Reusing a class's behavior and data", "Deleting classes", "Preventing object creation"], 0, "The child class inherits what the parent class provides."),
            _q("How do we specify the parent class?", ["In parentheses after the child class name", "After print()", "Inside a comment"], 0, "We write class Child(Parent)."),
            _q("What does overriding a method mean?", ["Providing a new implementation in the child class", "Deleting the object", "Converting it into a variable"], 0, "The child class can customize the method's behavior."),
            _q("If Cat does not define a speak method but inherits from Animal, what happens?", ["It can use the inherited method", "An error always occurs", "Animal is deleted"], 0, "The child class can use an inherited method.")
        ]
    },
    "Simple Calculator": {
        "content": "In the calculator project, we combine input, numeric conversion, operators, and conditions. We read two numbers and an arithmetic operation, then choose the appropriate operation. We must handle division by zero and display a suitable message for an unknown operation.",
        "examples": [
            {"title": "Adding Two Numbers", "code": "a = 8\nb = 3\nprint(a + b)", "output": "11", "explanation": "This is the basic arithmetic operation that the calculator will support."},
            {"title": "Choosing an Operation", "code": "a, b = 8, 2\noperation = '*'\nif operation == '+':\n    print(a + b)\nelif operation == '*':\n    print(a * b)", "output": "16", "explanation": "Conditions determine which operation the program executes."}
        ],
        "questions": [
            _q("Which concepts are used in the calculator?", ["Input, conditions, and operations", "Inheritance only", "Graphics only"], 0, "The project combines several basic programming concepts."),
            _q("Which case should be checked before division?", ["The divisor must not be zero", "The first number must be a string", "Numbers must always be positive"], 0, "Division by zero is undefined."),
            _q("What is the benefit of elif when choosing an operation?", ["Checking an alternative operation", "Stopping the application", "Storing the result"], 0, "Each branch represents a possible operation."),
            _q("Why should we test different operations?", ["To verify every execution path", "To change the project name", "To increase the number of files"], 0, "We must verify every operation and error case.")
        ]
    },
    "Number Guessing Game": {
        "content": "In the guessing game, the program chooses a secret number, and the user tries to guess it. We compare the guess with the secret number using if, elif, and else, and repeat attempts using while until the user guesses correctly. We can add an attempt counter and helpful messages.",
        "examples": [
            {"title": "Comparing a Guess", "code": "secret = 7\nguess = 5\nif guess < secret:\n    print('Too low')\nelif guess > secret:\n    print('Too high')\nelse:\n    print('Correct')", "output": "Too low", "explanation": "The guess is smaller than the secret number."},
            {"title": "Attempt Counter", "code": "attempts = 0\nattempts += 1\nprint(attempts)", "output": "1", "explanation": "The counter increases after each attempt."}
        ],
        "questions": [
            _q("Which loop is suitable for repeating guesses until success?", ["while", "class", "def only"], 0, "while continues until the winning condition is satisfied."),
            _q("What does guess < secret mean?", ["The guess is lower than the number", "The guess is correct", "The guess is higher"], 0, "The comparison determines the direction of the guess."),
            _q("What is the purpose of an attempt counter?", ["Counting the number of guesses", "Choosing the programming language", "Deleting the secret number"], 0, "The counter increases with every attempt."),
            _q("When does the game end?", ["When the correct number is guessed", "Before the first attempt", "When a message is printed"], 0, "The winning condition stops the repetition.")
        ]
    },
    "Student Management Mini Project": {
        "content": "The student management project combines dictionaries, lists, functions, and conditions. Each student can be represented by a dictionary containing a name and grade, while all students are stored in a list. We create functions for adding, displaying, and searching for students, as well as calculating the average. We also test cases such as an empty list or a student who does not exist.",
        "examples": [
            {"title": "Storing a Student", "code": 'student = {"name": "Rami", "grade": 88}\nprint(student["name"], student["grade"])', "output": "Rami 88", "explanation": "The dictionary groups the student's data under clear keys."},
            {"title": "Calculating the Average Grade", "code": "grades = [80, 90, 100]\naverage = sum(grades) / len(grades)\nprint(average)", "output": "90.0", "explanation": "sum() adds the grades, and len() counts how many grades there are."}
        ],
        "questions": [
            _q("Which data structure is suitable for storing student data using keys?", ["dict", "int", "bool"], 0, "A dictionary associates each field with a value."),

            _q("What data structure is suitable for grouping multiple students?", ["list", "float", "str only"], 0, "Student dictionaries can be stored inside a list."),
            _q("How do we calculate the average?", ["Total sum ÷ number of values", "Number of values ÷ total sum", "Highest grade only"], 0, "The arithmetic mean equals the sum of the values divided by their count."),
            _q("Which case should be tested when searching?", ["The student does not exist", "Changing the screen color", "Closing the editor"], 0, "An appropriate result should be displayed if the student is not found.")
        ]
    }
}


def get_python_lesson_content(lesson_title: str, lesson_description: str = ""):
    """Return lesson-specific educational material without changing SQLite."""
    item = PYTHON_LESSON_CONTENT.get(lesson_title)
    if item is None:
        return {
            "content": lesson_description or "Detailed lesson content will be added soon.",
            "examples": [],
            "questions": [],
        }
    return {
        "content": item["content"],
        "examples": item["examples"],
        "questions": item["questions"],
    }

JAVA_LESSON_CONTENT = {
    "What is Java?": {
        "content": "Java is a high-level, object-oriented programming language that runs on different operating systems through the Java Virtual Machine (JVM). It is used in enterprise applications, Android applications, and backend systems. Its main features include code organization, memory management, and the ability to run programs on multiple platforms when a compatible JVM is available.",
        "examples": [
            {
                "title": "Display a Message",
                "code": 'public class Main {\n    public static void main(String[] args) {\n        System.out.println("Hello Java");\n    }\n}',
                "output": "Hello Java",
                "explanation": "This is a simple Java program. Execution starts from main, and the text is displayed using System.out.println."
            },
            {
                "title": "Perform a Calculation",
                "code": 'public class Main {\n    public static void main(String[] args) {\n        int a = 5;\n        int b = 3;\n        System.out.println(a + b);\n    }\n}',
                "output": "8",
                "explanation": "We created two integer variables, added their values, and displayed the result."
            }
        ],
        "questions": [
            _q("What type of language is Java?", ["Programming language", "Operating system", "Database"], 0, "Java is a high-level programming language."),
            _q("Which component runs Java bytecode?", ["JVM", "HTML", "CSS"], 0, "The Java Virtual Machine is responsible for running bytecode."),
            _q("Where does Java program execution begin?", ["main", "startHTML", "print"], 0, "The main method is the primary entry point of a Java program."),
            _q("Which command prints text in Java?", ["System.out.println()", "input()", "echo()"], 0, "System.out.println displays text and then moves to a new line.")
        ]
    },

    "Inheritance": _js_lesson(
        "Inheritance allows a class to inherit properties and methods from another class. We use extends to create a subclass.",
        """class Animal {
    speak() {
        console.log("Animal sound");
    }
}

class Dog extends Animal {}

const dog = new Dog();
dog.speak();""",
        "Animal sound",
        "Which keyword is used to inherit from another class?",
        ["extends", "inherits", "import", "implements"],
        0,
        "The extends keyword is used to create a subclass."
    ),



    "Simple Calculator": _js_lesson(
        "In this project, we use functions and arithmetic operators to build a simple calculator. The function receives two numbers and an operator, then returns the appropriate result.",
        """function calculate(a, b, operator) {
    if (operator === "+") return a + b;
    if (operator === "-") return a - b;
    if (operator === "*") return a * b;
    if (operator === "/") return a / b;
    return "Invalid operator";
}

console.log(calculate(8, 2, "+"));
console.log(calculate(8, 2, "*"));""",
        "10\n16",
        "What is the result of calculate(8, 2, \"*\")?",
        ["16", "10", "6", "4"],
        0,
        "The * operator performs multiplication, so 8 × 2 = 16."
    ),

    "Student Grade Manager": _js_lesson(
        "In this project, we store student names and grades inside an array of objects. We can use map or forEach to iterate through the data and perform calculations to find the results.",
        """const students = [
    { name: "Ali", grade: 90 },
    { name: "Sara", grade: 80 }
];

const average = students.reduce(
    (sum, student) => sum + student.grade, 0
) / students.length;

console.log(average);""",
        "85",
        "What is the average of the grades 90 and 80?",
        ["85", "90", "80", "170"],
        0,
        "The average is calculated by dividing the sum of the grades by the number of grades: 170 ÷ 2 = 85."
    ),

    "To-Do List Project": _js_lesson(
        "In the To-Do List project, we use an array to store tasks, an object to represent each task, and functions to add or display tasks. This project combines arrays, objects, and functions.",
        """const tasks = [];

function addTask(title) {
    tasks.push({ title: title, completed: false });
}

addTask("Learn JavaScript");
addTask("Practice coding");

console.log(tasks.length);
console.log(tasks[0].title);""",
        "2\nLearn JavaScript",
        "Which method is used to add a task to the end of an array?",
        ["push()", "pop()", "includes()", "shift()"],
        0,
        "The push() method adds a new task to the end of the tasks array."
    ),
}


def get_javascript_lesson_content(
    lesson_title: str,
    lesson_description: str = ""
):
    item = JAVASCRIPT_LESSON_CONTENT.get(lesson_title)

    if item is None:
        return {
            "content": lesson_description or "Detailed lesson content will be added soon.",
            "examples": [],
            "questions": [],
        }

    return {
        "content": item["content"],
        "examples": item["examples"],
        "questions": item["questions"],
    }


def get_java_lesson_content(lesson_title: str, lesson_description: str = ""):
   
    item = JAVA_LESSON_CONTENT.get(lesson_title)

    if item is None:
        return {
            "content": lesson_description or "Detailed lesson content will be added soon.",
            "examples": [],
            "questions": [],
        }

    return {
        "content": item["content"],
        "examples": item["examples"],
        "questions": item["questions"],
    }

# =================================
# Student Course Placement APIs
# =================================

@app.get("/placement/status/{course_id}")
def get_placement_status(
    course_id: int,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_firebase_user),
):
    course = db.query(CourseModel).filter(
        CourseModel.id == course_id
    ).first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found."
        )

    placement = db.query(StudentCourseLevelModel).filter(
        StudentCourseLevelModel.student_id == current_user.id,
        StudentCourseLevelModel.course_id == course_id,
    ).first()

    if not placement:
        return {
            "has_level": False,
            "course_id": course_id,
            "level": None,
            "source": None,
        }

    return {
        "has_level": True,
        "course_id": course_id,
        "level": placement.level,
        "source": placement.source,
        "assessment_score": placement.assessment_score,
        "assessment_total": placement.assessment_total,
    }


@app.post("/placement/manual")
def set_manual_placement(
    request: ManualPlacementRequest,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_firebase_user),
):
    course = db.query(CourseModel).filter(
        CourseModel.id == request.course_id
    ).first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found."
        )

    existing = db.query(StudentCourseLevelModel).filter(
        StudentCourseLevelModel.student_id == current_user.id,
        StudentCourseLevelModel.course_id == request.course_id,
    ).first()

    if existing:
        raise HTTPException(
            status_code=409,
            detail="A learning level has already been selected for this course."
        )

    placement = StudentCourseLevelModel(
        student_id=current_user.id,
        course_id=request.course_id,
        level=request.level,
        source="manual",
    )

    db.add(placement)
    db.commit()
    db.refresh(placement)

    return {
        "message": "Learning level saved successfully.",
        "course_id": placement.course_id,
        "level": placement.level,
        "source": placement.source,
    }
# =================================
# Level Exam Request Schemas
# =================================

class ExamCreateRequest(BaseModel):
    course_id: int
    level: str = Field(
        pattern="^(beginner|intermediate|advanced)$"
    )
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    pass_score: int = Field(default=70, ge=1, le=100)
    duration_minutes: int | None = Field(default=None, ge=1)


class ExamQuestionCreateRequest(BaseModel):
    question_type: str = Field(
        pattern=(
            "^(multiple_choice|true_false|code_completion|"
            "code_ordering|find_bug|problem_solving|voice_answer)$"
        )
    )

    question_text: str = Field(min_length=1)
    options: list[dict] | None = None
    correct_answer: str | None = None
    explanation: str | None = None
    starter_code: str | None = None

    points: int = Field(default=1, ge=1)
    order_index: int = Field(default=1, ge=1)

    test_cases: list[TestCaseCreate] = Field(default_factory=list)


class ExamStartRequest(BaseModel):
    exam_id: int


class ExamAnswerSubmit(BaseModel):
    question_id: int
    answer_text: str | None = None
    submitted_code: str | None = None
    audio_transcript: str | None = None


class ExamSubmitRequest(BaseModel):
    pass
# =================================
# Placement Assessment APIs
# =================================

@app.get("/placement/questions/{course_id}")
def get_placement_questions(
    course_id: int,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_firebase_user),
):
    course = db.query(CourseModel).filter(
        CourseModel.id == course_id
    ).first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found."
        )

    existing = db.query(StudentCourseLevelModel).filter(
        StudentCourseLevelModel.student_id == current_user.id,
        StudentCourseLevelModel.course_id == course_id,
    ).first()

    if existing:
        raise HTTPException(
            status_code=409,
            detail="A learning level has already been selected."
        )

    language = get_course_placement_language(course.title)

    if language not in PLACEMENT_QUESTIONS:
        raise HTTPException(
            status_code=400,
            detail="Placement assessment is not available for this course."
        )

    questions = []

    for item in PLACEMENT_QUESTIONS[language]:
        questions.append({
            "question_id": item["id"],
            "level": item["level"],
            "question": item["question"],
            "options": item["options"],
        })

    return {
        "course_id": course_id,
        "course_title": course.title,
        "language": language,
        "total_questions": len(questions),
        "questions": questions,
    }


@app.post("/placement/submit")
def submit_placement_assessment(
    request: PlacementSubmitRequest,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_firebase_user),
):
    course = db.query(CourseModel).filter(
        CourseModel.id == request.course_id
    ).first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found."
        )

    existing = db.query(StudentCourseLevelModel).filter(
        StudentCourseLevelModel.student_id == current_user.id,
        StudentCourseLevelModel.course_id == request.course_id,
    ).first()

    if existing:
        raise HTTPException(
            status_code=409,
            detail="A learning level has already been selected."
        )

    language = get_course_placement_language(course.title)

    if language not in PLACEMENT_QUESTIONS:
        raise HTTPException(
            status_code=400,
            detail="Placement assessment is not available for this course."
        )

    question_bank = PLACEMENT_QUESTIONS[language]

    submitted_answers = {
        answer.question_id: answer.option_id
        for answer in request.answers
    }

    expected_ids = {
        question["id"] for question in question_bank
    }

    # Reject missing, duplicated, or unknown questions.
    if (
        len(submitted_answers) != len(question_bank)
        or set(submitted_answers.keys()) != expected_ids
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid or incomplete assessment answers."
        )

    score = 0
    stage_scores = {
        "beginner": 0,
        "intermediate": 0,
        "advanced": 0,
    }

    for question in question_bank:
        selected_option = submitted_answers[question["id"]]

        valid_options = {
            option["id"] for option in question["options"]
        }

        if selected_option not in valid_options:
            raise HTTPException(
                status_code=400,
                detail="Invalid answer option."
            )

        if selected_option == question["correct"]:
            score += 1
            stage_scores[question["level"]] += 1

    # Each stage has two questions.
    if stage_scores["beginner"] < 2:
        assigned_level = "beginner"

    elif stage_scores["intermediate"] < 2:
        assigned_level = "intermediate"

    else:
        assigned_level = "advanced"

    placement = StudentCourseLevelModel(
        student_id=current_user.id,
        course_id=request.course_id,
        level=assigned_level,
        source="assessment",
        assessment_score=score,
        assessment_total=len(question_bank),
    )

    db.add(placement)
    db.commit()
    db.refresh(placement)

    return {
        "message": "Placement assessment completed successfully.",
        "course_id": course.id,
        "course_title": course.title,
        "language": language,
        "level": assigned_level,
        "score": score,
        "total_questions": len(question_bank),
        "stage_scores": stage_scores,
        "source": "assessment",
    }

# =================================
# 17. Get lessons of a module
# =================================

@app.get("/modules/{module_id}/lessons")
def get_module_lessons(
    module_id: int,
    db: Session = Depends(get_db),
):
    module = db.query(ModuleModel).filter(
        ModuleModel.id == module_id
    ).first()

    if module is None:
        raise HTTPException(status_code=404, detail="Module not found")

    course = db.query(CourseModel).filter(
        CourseModel.id == module.course_id
    ).first()

    lessons = db.query(LessonModel).filter(
        LessonModel.module_id == module_id
    ).order_by(LessonModel.order_index).all()

    result = []

    for lesson in lessons:
        item = {
            "id": lesson.id,
            "module_id": lesson.module_id,
            "title": lesson.title,
            "description": lesson.description,
            "lesson_type": lesson.lesson_type,
            "duration_minutes": lesson.duration_minutes,
            "order_index": lesson.order_index,
        }

        if course and course.title == "Java (OOP)":
            item.update(
                get_java_lesson_content(
                    lesson.title,
                    lesson.description or ""
                )
            )

        elif course and course.title == "JavaScript Essentials":
            item.update(
                get_javascript_lesson_content(
                    lesson.title,
                    lesson.description or ""
                )
            )

        else:
            item.update(
                get_python_lesson_content(
                    lesson.title,
                    lesson.description or ""
                )
            )

        result.append(item)

    return result


# =================================
# 18. Complete lesson and award XP
# =================================

@app.post("/lessons/{lesson_id}/complete")
def complete_lesson(
    lesson_id: int,
    payload: CompleteLessonRequest,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_firebase_user),
):
    student_id = current_user.id
    if not student_id.strip():
        raise HTTPException(status_code=400, detail="Student ID cannot be empty")

    if payload.total_questions <= 0:
        raise HTTPException(
            status_code=400,
            detail="Total questions must be greater than zero",
        )

    if not 0 <= payload.quiz_score <= payload.total_questions:
        raise HTTPException(status_code=400, detail="Invalid quiz score")

    lesson = db.query(LessonModel).filter(
        LessonModel.id == lesson_id
    ).first()

    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")

    try:
        progress = db.query(LessonProgressModel).filter(
            LessonProgressModel.student_id == student_id,
            LessonProgressModel.lesson_id == lesson_id,
        ).first()

        if progress is None:
            db.add(
                LessonProgressModel(
                    student_id=student_id,
                    lesson_id=lesson_id,
                    completed=1,
                    quiz_score=payload.quiz_score,
                    total_questions=payload.total_questions,
                )
            )
        else:
            progress.completed = 1
            progress.quiz_score = payload.quiz_score
            progress.total_questions = payload.total_questions

        xp_transaction = db.query(XPTransactionModel).filter(
            XPTransactionModel.student_id == student_id,
            XPTransactionModel.lesson_id == lesson_id,
        ).first()

        xp_awarded = 0
        if xp_transaction is None:
            db.add(
                XPTransactionModel(
                    student_id=student_id,
                    lesson_id=lesson_id,
                    amount=10,
                )
            )
            xp_awarded = 10

        db.commit()

        xp_amounts = db.query(XPTransactionModel.amount).filter(
            XPTransactionModel.student_id == student_id
        ).all()
        total_xp = sum(amount for (amount,) in xp_amounts)

        return {
            "message": "Lesson completed successfully",
            "lesson_id": lesson_id,
            "completed": True,
            "quiz_score": payload.quiz_score,
            "total_questions": payload.total_questions,
            "xp_awarded": xp_awarded,
            "total_xp": total_xp,
        }

    except Exception:
        db.rollback()
        raise


# =================================
# 19. Get student progress and XP
# =================================

@app.get("/students/{student_id}/progress")
def get_student_progress(
    student_id: str,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_firebase_user),
):
    student_id = current_user.id
    # Get completed lesson progress
    progress_records = db.query(LessonProgressModel).filter(
        LessonProgressModel.student_id == student_id
    ).all()

    # Get XP earned from lessons
    lesson_xp_records = db.query(XPTransactionModel).filter(
        XPTransactionModel.student_id == student_id
    ).all()

    # Get XP earned from exercises
    exercise_xp_records = db.query(
        ExerciseXPTransactionModel
    ).filter(
        ExerciseXPTransactionModel.student_id == student_id
    ).all()

    # Calculate XP totals
    lesson_xp = sum(record.amount for record in lesson_xp_records)
    exercise_xp = sum(record.amount for record in exercise_xp_records)

    total_xp = lesson_xp + exercise_xp

    # Count completed lessons
    completed_lessons = sum(
        1 for record in progress_records
        if record.completed == 1
    )

    # XP required for each level
    level_thresholds = {
        1: 0,
        2: 100,
        3: 250,
        4: 500,
        5: 800,
        6: 1200,
        7: 1700,
        8: 2300,
        9: 3000,
        10: 4000,
    }

    # Calculate current level
    current_level = 1

    for level, required_xp in level_thresholds.items():
        if total_xp >= required_xp:
            current_level = level

    return {
        "student_id": student_id,
        "total_xp": total_xp,
        "lesson_xp": lesson_xp,
        "exercise_xp": exercise_xp,
        "current_level": current_level,
        "completed_lessons": completed_lessons,
        "lessons": [
            {
                "lesson_id": record.lesson_id,
                "completed": bool(record.completed),
                "quiz_score": record.quiz_score,
                "total_questions": record.total_questions,
            }
            for record in progress_records
        ],
    }

# ==========================================
# 21. Run Python code against a test case
# Docker Sandbox
# ==========================================

def run_python_test_case(code: str, input_data: str):

    docker_path = shutil.which("docker")

    if docker_path is None:
        raise HTTPException(
            status_code=503,
            detail="Docker is not installed or not running"
        )

    BASE_WORKSPACE = (
        r"F:\flutter_projects\codequest\docker_workspace"
    )

    os.makedirs(BASE_WORKSPACE, exist_ok=True)

    execution_id = uuid.uuid4().hex
    temp_dir = os.path.join(BASE_WORKSPACE, execution_id)

    os.makedirs(temp_dir)

    container_name = f"codequest-{execution_id[:12]}"

    try:
        file_path = os.path.join(temp_dir, "main.py")

        with open(file_path, "w", encoding="utf-8") as file:
            file.write(code)

        command = [
            docker_path,
            "run",
            "--rm",
            "-i",
            "--name", container_name,

            "--network", "none",
            "--memory", "128m",
            "--cpus", "0.5",
            "--pids-limit", "32",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges=true",
            "--user", "65534:65534",

            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",

            "--mount",
            f"type=bind,source={temp_dir},target=/workspace,readonly",

            "-w", "/workspace",
            "-e", "PYTHONDONTWRITEBYTECODE=1",

            "python:3.12-alpine",
            "python",
            "-I",
            "/workspace/main.py"
        ]

        try:

            print("[CodeQuest] Input data:", repr(input_data))
            print("[CodeQuest] Input data type:", type(input_data))
            print("[CodeQuest] Starting Docker test...")


            result = subprocess.run(
                command,
                input=input_data,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15
            )
            print("[CodeQuest] Docker test finished.") 
        except subprocess.TimeoutExpired:
            try:
                subprocess.run(
                    [
                        docker_path,
                        "rm",
                        "-f",
                        container_name
                    ],
                    capture_output=True,
                    timeout=5
                )
            except (subprocess.TimeoutExpired, OSError):
                pass

            return {
                "status": "timeout",
                "output": "",
                "stderr": "Execution time limit exceeded.",
                "exit_code": None
            }

        stdout = result.stdout[:20000]
        stderr = result.stderr[:20000]

        if result.returncode == 0:
            return {
                "status": "success",
                "output": stdout,
                "stderr": "",
                "exit_code": 0
            }

        return {
            "status": "error",
            "output": stdout,
            "stderr": stderr,
            "exit_code": result.returncode
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Execution service error: {str(e)}"
        )

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def run_java_test_case(code: str, input_data: str):

    docker_path = shutil.which("docker")

    if docker_path is None:
        raise HTTPException(
            status_code=503,
            detail="Docker is not installed or not running"
        )

    BASE_WORKSPACE = (
        r"F:\flutter_projects\codequest\docker_workspace"
    )

    os.makedirs(BASE_WORKSPACE, exist_ok=True)

    execution_id = uuid.uuid4().hex
    temp_dir = os.path.join(BASE_WORKSPACE, execution_id)
    os.makedirs(temp_dir)

    container_name = f"codequest-java-{execution_id[:12]}"

    try:
        file_path = os.path.join(temp_dir, "Main.java")

        with open(file_path, "w", encoding="utf-8") as file:
            file.write(code)

        command = [
            docker_path,
            "run",
            "--rm",
            "-i",
            "--name", container_name,

            "--network", "none",
            "--memory", "256m",
            "--cpus", "0.5",
            "--pids-limit", "32",

            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges=true",
            "--user", "65534:65534",

            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",

            "--mount",
            f"type=bind,source={temp_dir},target=/workspace,readonly",

            "-w", "/workspace",

            "eclipse-temurin:21-jdk-alpine",

            "sh",
            "-c",
            "mkdir -p /tmp/classes && "
            "javac -encoding UTF-8 -d /tmp/classes /workspace/Main.java && "
            "java -Xmx64m -Duser.home=/tmp -cp /tmp/classes Main"
        ]

        try:
            result = subprocess.run(
                command,
                input=input_data,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=25
            )

        except subprocess.TimeoutExpired:
            try:
                subprocess.run(
                    [
                        docker_path,
                        "rm",
                        "-f",
                        container_name
                    ],
                    capture_output=True,
                    timeout=5
                )
            except (subprocess.TimeoutExpired, OSError):
                pass

            return {
                "status": "timeout",
                "output": "",
                "stderr": "Java execution time limit exceeded.",
                "exit_code": None
            }

        stdout = result.stdout[:20000]
        stderr = result.stderr[:20000]

        if result.returncode == 0:
            return {
                "status": "success",
                "output": stdout,
                "stderr": "",
                "exit_code": 0
            }

        return {
            "status": "error",
            "output": stdout,
            "stderr": stderr,
            "exit_code": result.returncode
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Java execution service error: {str(e)}"
        )

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def run_javascript_test_case(code: str, input_data: str):

    docker_path = shutil.which("docker")

    if docker_path is None:
        raise HTTPException(
            status_code=503,
            detail="Docker is not installed or not running"
        )

    BASE_WORKSPACE = (
        r"F:\flutter_projects\codequest\docker_workspace"
    )

    os.makedirs(BASE_WORKSPACE, exist_ok=True)

    execution_id = uuid.uuid4().hex
    temp_dir = os.path.join(BASE_WORKSPACE, execution_id)
    os.makedirs(temp_dir)

    container_name = f"codequest-js-{execution_id[:12]}"

    try:
        file_path = os.path.join(temp_dir, "main.js")

        with open(file_path, "w", encoding="utf-8") as file:
            file.write(code)

        command = [
            docker_path,
            "run",
            "--rm",
            "-i",
            "--name", container_name,

            "--network", "none",
            "--memory", "128m",
            "--cpus", "0.5",
            "--pids-limit", "32",

            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges=true",
            "--user", "65534:65534",

            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",

            "--mount",
            f"type=bind,source={temp_dir},target=/workspace,readonly",

            "-w", "/workspace",

            "node:22-alpine",
            "node",
            "--max-old-space-size=64",
            "/workspace/main.js"
        ]

        try:
            result = subprocess.run(
                command,
                input=input_data,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15
            )

        except subprocess.TimeoutExpired:
            try:
                subprocess.run(
                    [
                        docker_path,
                        "rm",
                        "-f",
                        container_name
                    ],
                    capture_output=True,
                    timeout=5
                )
            except (subprocess.TimeoutExpired, OSError):
                pass

            return {
                "status": "timeout",
                "output": "",
                "stderr": "JavaScript execution time limit exceeded.",
                "exit_code": None
            }

        stdout = result.stdout[:20000]
        stderr = result.stderr[:20000]

        if result.returncode == 0:
            return {
                "status": "success",
                "output": stdout,
                "stderr": "",
                "exit_code": 0
            }

        return {
            "status": "error",
            "output": stdout,
            "stderr": stderr,
            "exit_code": result.returncode
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"JavaScript execution service error: {str(e)}"
        )

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
def run_code_test_case(code: str, input_data: str, language: str):

    language = language.strip().lower()

    if language == "python":
        return run_python_test_case(code, input_data)

    elif language == "java":
        return run_java_test_case(code, input_data)

    elif language in ("javascript", "js"):
        return run_javascript_test_case(code, input_data)

    raise HTTPException(
        status_code=400,
        detail=f"Unsupported language: {language}"
    )
# ==========================================
# 22. Submit Exercise
# Evaluate code and award XP once
# ==========================================

@app.post("/exercises/{exercise_id}/submit")
def submit_exercise(
    exercise_id: int,
    payload: ExerciseSubmitRequest,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_firebase_user),
):
    student_id = current_user.id

    # Validate code
    code = payload.code

    if not code.strip():
        raise HTTPException(
            status_code=400,
            detail="Code cannot be empty"
        )

    if len(code) > 10000:
        raise HTTPException(
            status_code=413,
            detail="Code is too large"
        )

    # Find exercise
    exercise = db.query(ExerciseModel).filter(
        ExerciseModel.id == exercise_id
    ).first()

    if exercise is None:
        raise HTTPException(
            status_code=404,
            detail="Exercise not found"
        )

    language = exercise.language.strip().lower()

    if language == "js":
        language = "javascript"

    if language not in ("python", "java", "javascript"):
        raise HTTPException(
            status_code=400,
            detail="Unsupported exercise language"
        )

    # Get all test cases, including hidden ones
    test_cases = db.query(TestCaseModel).filter(
        TestCaseModel.exercise_id == exercise_id
    ).order_by(TestCaseModel.id).all()

    if not test_cases:
        raise HTTPException(
            status_code=400,
            detail="This exercise has no test cases"
        )

    # Limit the number of test cases per submission
    if len(test_cases) > 20:
        raise HTTPException(
            status_code=400,
            detail="This exercise has too many test cases"
        )

    passed_tests = 0
    execution_error = False
    timed_out = False

    saved_output = []
    saved_stderr = []

    # Run code against every test case
    for index, test_case in enumerate(test_cases, start=1):

        result = run_code_test_case(
        code=code,
          input_data=test_case.input_data,
          language=language
        )
        print(f"\n[CodeQuest] Test {index} status: {result['status']}")

        if result["status"] != "success":
            print("[CodeQuest] Docker stderr:")
            print(result["stderr"])
            print("[CodeQuest] Exit code:")
            print(result["exit_code"])
        actual_output = result["output"]

        if result["status"] == "timeout":
            timed_out = True
            execution_error = True

            saved_stderr.append(
                f"Test {index}: Execution time limit exceeded."
            )

            break

        if result["status"] == "error":
            execution_error = True

            saved_stderr.append(
                f"Test {index}: {result['stderr']}"
            )

            break

        # Normalize line endings and surrounding whitespace
        actual_normalized = actual_output.replace(
            "\r\n", "\n"
        ).strip()

        expected_normalized = test_case.expected_output.replace(
            "\r\n", "\n"
        ).strip()

        if actual_normalized == expected_normalized:
            passed_tests += 1

        # Save execution information internally
        saved_output.append(
            f"Test {index}: {actual_output}"
        )

    total_tests = len(test_cases)

    score = round(
        (passed_tests / total_tests) * 100
    )

    # Determine submission status
    if timed_out:
        submission_status = "timeout"

    elif execution_error:
        submission_status = "error"

    elif passed_tests == total_tests:
        submission_status = "accepted"

    else:
        submission_status = "rejected"

    output_text = "\n".join(saved_output)[:20000]
    stderr_text = "\n".join(saved_stderr)[:20000]

    xp_awarded = 0

    try:
        # Save submission
        submission = SubmissionModel(
            student_id=student_id,
            exercise_id=exercise_id,
            code=code,
            status=submission_status,
            output=output_text,
            stderr=stderr_text,
            score=score
        )

        db.add(submission)

        # Award XP only when all test cases pass
        if submission_status == "accepted":

            existing_xp = db.query(
                ExerciseXPTransactionModel
            ).filter(
                ExerciseXPTransactionModel.student_id
                == student_id,
                ExerciseXPTransactionModel.exercise_id
                == exercise_id
            ).first()

            if existing_xp is None:

                db.add(
                    ExerciseXPTransactionModel(
                        student_id=student_id,
                        exercise_id=exercise_id,
                        amount=exercise.xp_reward
                    )
                )

                xp_awarded = exercise.xp_reward

        db.commit()

    except IntegrityError:

        # Another request may have awarded XP
        db.rollback()

        if submission_status == "accepted":

            # Save the submission without awarding XP again
            submission = SubmissionModel(
                student_id=student_id,
                exercise_id=exercise_id,
                code=code,
                status=submission_status,
                output=output_text,
                stderr=stderr_text,
                score=score
            )

            db.add(submission)
            db.commit()

            xp_awarded = 0

        else:
            raise HTTPException(
                status_code=500,
                detail="Could not save submission"
            )

    except Exception:
        db.rollback()
        raise

    # Calculate total XP from lessons and exercises
    lesson_xp_records = db.query(
        XPTransactionModel.amount
    ).filter(
        XPTransactionModel.student_id == student_id
    ).all()

    exercise_xp_records = db.query(
        ExerciseXPTransactionModel.amount
    ).filter(
        ExerciseXPTransactionModel.student_id
        == student_id
    ).all()

    total_xp = (
        sum(amount for (amount,) in lesson_xp_records)
        +
        sum(amount for (amount,) in exercise_xp_records)
    )

    return {
        "message": "Exercise submitted successfully",
        "exercise_id": exercise_id,
        "status": submission_status,
        "passed_tests": passed_tests,
        "total_tests": total_tests,
        "score": score,
        "xp_awarded": xp_awarded,
        "total_xp": total_xp
    }


# ==========================================
# 20. Exercise APIs
# ==========================================

# Get all exercises for a lesson
@app.get("/lessons/{lesson_id}/exercises")
def get_lesson_exercises(
    lesson_id: int,
    db: Session = Depends(get_db),
):
    lesson = db.query(LessonModel).filter(
        LessonModel.id == lesson_id
    ).first()

    if lesson is None:
        raise HTTPException(
            status_code=404,
            detail="Lesson not found"
        )

    exercises = db.query(ExerciseModel).filter(
        ExerciseModel.lesson_id == lesson_id
    ).order_by(ExerciseModel.order_index).all()

    return [
        {
            "id": exercise.id,
            "lesson_id": exercise.lesson_id,
            "title": exercise.title,
            "description": exercise.description,
            "instructions": exercise.instructions,
            "starter_code": exercise.starter_code,
            "language": exercise.language,
            "difficulty": exercise.difficulty,
            "xp_reward": exercise.xp_reward,
            "order_index": exercise.order_index,
        }
        for exercise in exercises
    ]


# Get exercise details and visible test cases
@app.get("/exercises/{exercise_id}")
def get_exercise(
    exercise_id: int,
    db: Session = Depends(get_db),
):
    exercise = db.query(ExerciseModel).filter(
        ExerciseModel.id == exercise_id
    ).first()

    if exercise is None:
        raise HTTPException(
            status_code=404,
            detail="Exercise not found"
        )

    test_cases = db.query(TestCaseModel).filter(
        TestCaseModel.exercise_id == exercise_id,
        TestCaseModel.is_hidden == False
    ).all()

    return {
        "id": exercise.id,
        "lesson_id": exercise.lesson_id,
        "title": exercise.title,
        "description": exercise.description,
        "instructions": exercise.instructions,
        "starter_code": exercise.starter_code,
        "language": exercise.language,
        "difficulty": exercise.difficulty,
        "xp_reward": exercise.xp_reward,
        "test_cases": [
            {
                "id": case.id,
                "input_data": case.input_data,
                "expected_output": case.expected_output,
            }
            for case in test_cases
        ],
    }


# Create a new exercise with test cases
@app.post("/exercises")
def create_exercise(
    payload: ExerciseCreate,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_expert),
):
    lesson = db.query(LessonModel).filter(
        LessonModel.id == payload.lesson_id
    ).first()

    if lesson is None:
        raise HTTPException(
            status_code=404,
            detail="Lesson not found"
        )

    if not payload.title.strip():
        raise HTTPException(
            status_code=400,
            detail="Exercise title cannot be empty"
        )

    if payload.xp_reward < 0 or payload.order_index < 1:
        raise HTTPException(
            status_code=400,
            detail="Invalid XP reward or order index"
        )

    if not payload.test_cases:
        raise HTTPException(
            status_code=400,
            detail="At least one test case is required"
        )

    if len(payload.test_cases) > 20:
        raise HTTPException(
            status_code=400,
            detail="An exercise cannot have more than 20 test cases"
        )

    for case in payload.test_cases:
        if (
            not isinstance(case.input_data, str)
            or not isinstance(case.expected_output, str)
        ):
            raise HTTPException(
                status_code=400,
                detail="Test case input and expected output must be text"
            )
        if not case.input_data.strip() and not case.expected_output.strip():
            raise HTTPException(
                status_code=400,
                detail="Test case input and expected output cannot both be empty"
            )

    if payload.language.strip().lower() not in ("python", "java", "javascript", "js"):
       raise HTTPException(
            status_code=400,
            detail="Unsupported exercise language"
        )
    try:
        exercise = ExerciseModel(
            lesson_id=payload.lesson_id,
            title=payload.title,
            description=payload.description,
            instructions=payload.instructions,
            starter_code=payload.starter_code,
            language=payload.language,
            difficulty=payload.difficulty,
            xp_reward=payload.xp_reward,
            order_index=payload.order_index,
        )

        db.add(exercise)
        db.flush()

        for case in payload.test_cases:
            db.add(
                TestCaseModel(
                    exercise_id=exercise.id,
                    input_data=case.input_data,
                    expected_output=case.expected_output,
                    is_hidden=case.is_hidden,
                )
            )

        db.commit()
        db.refresh(exercise)

        return {
            "message": "Exercise created successfully",
            "exercise_id": exercise.id,
            "title": exercise.title,
            "test_cases_count": len(payload.test_cases),
        }

    except Exception:
        db.rollback()
        raise
@app.put("/admin/exercises/{exercise_id}")
def admin_update_exercise(
    exercise_id: int,
    payload: ExerciseUpdate,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_expert),
):
    exercise = db.query(ExerciseModel).filter(
        ExerciseModel.id == exercise_id
    ).first()

    if exercise is None:
        raise HTTPException(
            status_code=404,
            detail="Exercise not found"
        )

    updates = payload.dict(exclude_unset=True)

    if not updates:
        raise HTTPException(
            status_code=400,
            detail="No fields provided for update"
        )

    if "title" in updates and not updates["title"].strip():
        raise HTTPException(
            status_code=400,
            detail="Exercise title cannot be empty"
        )

    if "description" in updates and not updates["description"].strip():
        raise HTTPException(
            status_code=400,
            detail="Exercise description cannot be empty"
        )

    if "language" in updates:
        allowed_languages = ["python", "java", "javascript", "js"]

        if updates["language"] not in allowed_languages:
            raise HTTPException(
                status_code=400,
                detail="Unsupported programming language"
            )

        if updates["language"] != exercise.language:
            has_submissions = db.query(SubmissionModel).filter(
                SubmissionModel.exercise_id == exercise_id
            ).first()

            if has_submissions:
                raise HTTPException(
                    status_code=409,
                    detail="Cannot change language because students have submissions"
                )

    if "xp_reward" in updates and updates["xp_reward"] < 0:
        raise HTTPException(
            status_code=400,
            detail="XP reward cannot be negative"
        )

    if "order_index" in updates and updates["order_index"] < 1:
        raise HTTPException(
            status_code=400,
            detail="Order index must be at least 1"
        )

    for field, value in updates.items():
        setattr(exercise, field, value)

    db.commit()
    db.refresh(exercise)

    return {
        "message": "Exercise updated successfully",
        "exercise_id": exercise.id,
        "title": exercise.title
    }
@app.delete("/admin/exercises/{exercise_id}")
def admin_delete_exercise(
    exercise_id: int,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_expert),
):
    exercise = db.query(ExerciseModel).filter(
        ExerciseModel.id == exercise_id
    ).first()

    if exercise is None:
        raise HTTPException(
            status_code=404,
            detail="Exercise not found"
        )

    has_submissions = db.query(SubmissionModel).filter(
        SubmissionModel.exercise_id == exercise_id
    ).first()

    has_xp = db.query(ExerciseXPTransactionModel).filter(
        ExerciseXPTransactionModel.exercise_id == exercise_id
    ).first()

    if has_submissions or has_xp:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete exercise because it has student submissions or XP records"
        )

    db.query(TestCaseModel).filter(
        TestCaseModel.exercise_id == exercise_id
    ).delete(synchronize_session=False)

    db.delete(exercise)
    db.commit()

    return {
        "message": "Exercise deleted successfully",
        "exercise_id": exercise_id
    }


# =================================
# Level Exams - Admin Management
# =================================

@app.post("/admin/exams")
def admin_create_exam(
    payload: ExamCreateRequest,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_expert),
):
    # Check whether the course exists.
    course = db.query(CourseModel).filter(
        CourseModel.id == payload.course_id
    ).first()

    if course is None:
        raise HTTPException(
            status_code=404,
            detail="Course not found"
        )

    # Prevent duplicate exams for the same course and level.
    existing_exam = db.query(LevelExamModel).filter(
        LevelExamModel.course_id == payload.course_id,
        LevelExamModel.level == payload.level
    ).first()

    if existing_exam:
        raise HTTPException(
            status_code=409,
            detail="An exam already exists for this course and level"
        )

    exam = LevelExamModel(
        course_id=payload.course_id,
        level=payload.level,
        title=payload.title.strip(),
        description=payload.description,
        pass_score=payload.pass_score,
        duration_minutes=payload.duration_minutes,
        is_active=True,
    )

    db.add(exam)
    db.commit()
    db.refresh(exam)

    return {
        "message": "Level exam created successfully",
        "exam_id": exam.id,
        "course_id": exam.course_id,
        "level": exam.level,
        "title": exam.title,
        "pass_score": exam.pass_score,
    }
# =================================
# Level Exams - Student APIs
# =================================

@app.get("/courses/{course_id}/level-exam")
def get_course_level_exam(
    course_id: int,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_firebase_user),
):
    # Check whether the course exists.
    course = db.query(CourseModel).filter(
        CourseModel.id == course_id
    ).first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found.",
        )

    # Get the student's current learning level.
  
    student_level = db.query(StudentCourseLevelModel).filter(
        StudentCourseLevelModel.student_id == current_user.id,
        StudentCourseLevelModel.course_id == course_id,
    ).first()

    if not student_level:
        raise HTTPException(
            status_code=403,
            detail="Please select your learning level first.",
        )

    # Do not show exams after the course is completed.
    if student_level.is_completed:
        return {
            "has_exam": False,
            "course_id": course_id,
            "course_title": course.title,
            "message": "Course already completed.",
            "exam": None,
        }


    # Find the exam for the student's current level.
    exam = db.query(LevelExamModel).filter(
        LevelExamModel.course_id == course_id,
        LevelExamModel.level == student_level.level,
        LevelExamModel.is_active == True,
    ).first()

    if not exam:
        return {
            "has_exam": False,
            "course_id": course_id,
            "course_title": course.title,
            "current_level": student_level.level,
            "exam": None,
        }

    # Count active questions without exposing their answers.
    questions_count = db.query(ExamQuestionModel).filter(
        ExamQuestionModel.exam_id == exam.id,
        ExamQuestionModel.is_active == True,
    ).count()

    return {
        "has_exam": True,
        "course_id": course_id,
        "course_title": course.title,
        "current_level": student_level.level,
        "exam": {
            "id": exam.id,
            "title": exam.title,
            "description": exam.description,
            "level": exam.level,
            "pass_score": exam.pass_score,
            "duration_minutes": exam.duration_minutes,
            "total_questions": questions_count,
        },
    }

# =================================
# Start or Resume a Level Exam
# =================================

@app.post("/exams/{exam_id}/start")
def start_level_exam(
    exam_id: int,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_firebase_user),
):
    # 1. Get the exam.
    exam = db.query(LevelExamModel).filter(
        LevelExamModel.id == exam_id,
        LevelExamModel.is_active == True,
    ).first()

    if not exam:
        raise HTTPException(
            status_code=404,
            detail="Active exam not found.",
        )

    # 2. Get the student's learning level for this course.
    student_level = db.query(StudentCourseLevelModel).filter(
        StudentCourseLevelModel.student_id == current_user.id,
        StudentCourseLevelModel.course_id == exam.course_id,
    ).first()

    if not student_level:
        raise HTTPException(
            status_code=403,
            detail="Please select your learning level first.",
        )
    if student_level.is_completed:
        raise HTTPException(
            status_code=403,
            detail="This course has already been completed.",
        )
    # 3. Make sure the exam matches the student's current level.
    if student_level.level != exam.level:
        raise HTTPException(
            status_code=403,
            detail="This exam does not match your current learning level.",
        )

    # 4. Check whether the student already has an unfinished attempt.
    existing_attempt = db.query(ExamAttemptModel).filter(
        ExamAttemptModel.student_id == current_user.id,
        ExamAttemptModel.exam_id == exam_id,
        ExamAttemptModel.status == "in_progress",
    ).order_by(
        ExamAttemptModel.started_at.desc()
    ).first()

    # 5. Resume an existing attempt if available.
    if existing_attempt:
        answer_rows = db.query(ExamAnswerModel).filter(
            ExamAnswerModel.attempt_id == existing_attempt.id
        ).order_by(
            ExamAnswerModel.id
        ).all()

        if answer_rows:
            question_ids = [
                answer.question_id for answer in answer_rows
            ]

            found_questions = db.query(ExamQuestionModel).filter(
                ExamQuestionModel.id.in_(question_ids)
            ).all()

            question_map = {
                question.id: question
                for question in found_questions
            }

            if len(question_map) != len(question_ids):
                raise HTTPException(
                    status_code=409,
                    detail="Some questions in this attempt are unavailable.",
                )

            questions = [
                question_map[question_id]
                for question_id in question_ids
            ]

        else:
            # Initialize an older unfinished attempt if it has no saved questions.
            questions = db.query(ExamQuestionModel).filter(
                ExamQuestionModel.exam_id == exam_id,
                ExamQuestionModel.is_active == True,
            ).order_by(
                ExamQuestionModel.order_index
            ).all()

            if not questions:
                raise HTTPException(
                    status_code=400,
                    detail="This exam has no active questions.",
                )

            try:
                for question in questions:
                    db.add(
                        ExamAnswerModel(
                            attempt_id=existing_attempt.id,
                            question_id=question.id,
                        )
                    )

                existing_attempt.total_points = sum(
                    question.points for question in questions
                )

                db.commit()

            except Exception:
                db.rollback()
                raise

    else:
        # 6. Get active exam questions.
        questions = db.query(ExamQuestionModel).filter(
            ExamQuestionModel.exam_id == exam_id,
            ExamQuestionModel.is_active == True,
        ).order_by(
            ExamQuestionModel.order_index
        ).all()

        if not questions:
            raise HTTPException(
                status_code=400,
                detail="This exam has no active questions.",
            )

        # 7. Create a new attempt and save its question snapshot.
        total_points = sum(
            question.points for question in questions
        )

        try:
            attempt = ExamAttemptModel(
                student_id=current_user.id,
                exam_id=exam_id,
                score=0,
                total_points=total_points,
                percentage=0.0,
                passed=False,
                status="in_progress",
            )

            db.add(attempt)
            db.flush()

            for question in questions:
                db.add(
                    ExamAnswerModel(
                        attempt_id=attempt.id,
                        question_id=question.id,
                    )
                )

            db.commit()
            db.refresh(attempt)

            existing_attempt = attempt

        except Exception:
            db.rollback()
            raise

    # Load saved answers for this exam attempt.
    saved_answer_rows = db.query(ExamAnswerModel).filter(
        ExamAnswerModel.attempt_id == existing_attempt.id
    ).all()

    saved_answers_map = {
        answer.question_id: answer
        for answer in saved_answer_rows
    }

    # 8. Prepare safe questions for Flutter.
    safe_questions = []

    for question in questions:
        options = None

        if question.options_json:
            try:
                options = json.loads(question.options_json)
            except (json.JSONDecodeError, TypeError):
                options = None

       
        saved_answer = saved_answers_map.get(question.id)

        safe_questions.append({
            "question_id": question.id,
            "question_type": question.question_type,
            "question_text": question.question_text,
            "options": options,
            "starter_code": question.starter_code,
            "points": question.points,
            "order_index": question.order_index,

            # Restore the student's saved answer.
            "answer_text": (
                saved_answer.answer_text if saved_answer else None
            ),
            "submitted_code": (
                saved_answer.submitted_code if saved_answer else None
            ),
            "audio_transcript": (
                saved_answer.audio_transcript if saved_answer else None
            ),
        })


    return {
        "message": "Exam started successfully.",
        "attempt_id": existing_attempt.id,
        "exam_id": exam.id,
        "exam_title": exam.title,
        "course_id": exam.course_id,
        "level": exam.level,
        "pass_score": exam.pass_score,
        "duration_minutes": exam.duration_minutes,
        "started_at": existing_attempt.started_at.isoformat(),
        "status": existing_attempt.status,
        "total_questions": len(safe_questions),
        "total_points": existing_attempt.total_points,
        "questions": safe_questions,
    }


@app.put("/exam-attempts/{attempt_id}/answers")
def save_exam_answer(
    attempt_id: int,
    payload: ExamAnswerSubmit,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_firebase_user),
):
    # 1. Find the exam attempt and verify ownership.
    attempt = db.query(ExamAttemptModel).filter(
        ExamAttemptModel.id == attempt_id,
        ExamAttemptModel.student_id == current_user.id
    ).first()

    if not attempt:
        raise HTTPException(
            status_code=404,
            detail="Exam attempt not found."
        )

    # 2. Check that the exam attempt is still active.
    if attempt.status != "in_progress":
        raise HTTPException(
            status_code=409,
            detail="This exam attempt is no longer active."
        )

    # 3. Get the exam.
    exam = db.query(LevelExamModel).filter(
        LevelExamModel.id == attempt.exam_id
    ).first()

    if not exam:
        raise HTTPException(
            status_code=404,
            detail="Exam not found."
        )

    # 4. Check the exam start time.
    if attempt.started_at is None:
        raise HTTPException(
            status_code=409,
            detail="Exam start time is unavailable."
        )

    if exam.duration_minutes is not None:
        elapsed_time = datetime.utcnow() - attempt.started_at
        time_limit = timedelta(minutes=exam.duration_minutes)

        if elapsed_time >= time_limit:
            raise HTTPException(
                status_code=409,
                detail="Exam time has expired. Submit the exam."
            )

    # 5. Verify that the question belongs to this exam.
    question = db.query(ExamQuestionModel).filter(
        ExamQuestionModel.id == payload.question_id,
        ExamQuestionModel.exam_id == attempt.exam_id
    ).first()

    if not question:
        raise HTTPException(
            status_code=404,
            detail="Question not found in this exam."
        )

    # 6. Validate the submitted answer.
    answer_fields = [
        payload.answer_text,
        payload.submitted_code,
        payload.audio_transcript
    ]

    provided_fields = sum(
        value is not None for value in answer_fields
    )

    if provided_fields > 1:
        raise HTTPException(
            status_code=400,
            detail="Submit only one answer type at a time."
        )

    for value in answer_fields:
        if value is not None and len(value) > 10000:
            raise HTTPException(
                status_code=400,
                detail="Answer is too long. Maximum length is 10000 characters."
            )

    # 7. Find the existing answer record.
    saved_answer = db.query(ExamAnswerModel).filter(
        ExamAnswerModel.attempt_id == attempt_id,
        ExamAnswerModel.question_id == payload.question_id
    ).first()

    if not saved_answer:
        raise HTTPException(
            status_code=404,
            detail="Answer record not found for this exam attempt."
        )

    # 8. Save or update the answer.
    saved_answer.answer_text = payload.answer_text
    saved_answer.submitted_code = payload.submitted_code
    saved_answer.audio_transcript = payload.audio_transcript

    # Reset previous grading information.
    saved_answer.is_correct = None
    saved_answer.score = 0
    saved_answer.feedback = None

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    # 9. Return a safe response.
    return {
        "message": "Answer saved successfully.",
        "attempt_id": attempt_id,
        "question_id": payload.question_id,
        "saved": True
    }

# ==========================================
# Submit and Grade a Level Exam
# ==========================================

@app.post("/exam-attempts/{attempt_id}/submit")
def submit_level_exam(
    attempt_id: int,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_firebase_user),
):
    # 1. Find the attempt and verify ownership.
    attempt = db.query(ExamAttemptModel).filter(
        ExamAttemptModel.id == attempt_id,
        ExamAttemptModel.student_id == current_user.id,
    ).first()

    if not attempt:
        raise HTTPException(
            status_code=404,
            detail="Exam attempt not found.",
        )

    if attempt.status != "in_progress":
        raise HTTPException(
            status_code=409,
            detail="This exam attempt has already been submitted.",
        )

    exam = db.query(LevelExamModel).filter(
        LevelExamModel.id == attempt.exam_id
    ).first()

    if not exam:
        raise HTTPException(
            status_code=404,
            detail="Exam not found.",
        )

    course = db.query(CourseModel).filter(
        CourseModel.id == exam.course_id
    ).first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found.",
        )

    student_level = db.query(StudentCourseLevelModel).filter(
        StudentCourseLevelModel.student_id == current_user.id,
        StudentCourseLevelModel.course_id == exam.course_id,
    ).first()

    if not student_level:
        raise HTTPException(
            status_code=403,
            detail="Student learning level was not found.",
        )

    if student_level.level != exam.level:
        raise HTTPException(
            status_code=403,
            detail="Student learning level does not match this exam.",
        )

    # 2. Load the saved question snapshot.
    saved_answers = db.query(ExamAnswerModel).filter(
        ExamAnswerModel.attempt_id == attempt_id
    ).order_by(
        ExamAnswerModel.id
    ).all()

    if not saved_answers:
        raise HTTPException(
            status_code=400,
            detail="This exam attempt has no saved questions.",
        )

    expected_ids = [
        answer.question_id for answer in saved_answers
    ]

    questions = {
        question.id: question
        for question in db.query(ExamQuestionModel).filter(
            ExamQuestionModel.id.in_(expected_ids)
        ).all()
    }

    if len(questions) != len(expected_ids):
        raise HTTPException(
            status_code=409,
            detail="Some exam questions are unavailable.",
        )

    # 3. Determine the programming language.
    language = get_course_placement_language(course.title)

    supported_languages = (
        "python",
        "java",
        "javascript",
    )

    has_code_questions = any(
        questions[qid].question_type in (
            "code_completion",
            "code_ordering",
            "find_bug",
            "problem_solving",
        )
        for qid in expected_ids
    )

    if has_code_questions and language not in supported_languages:
        raise HTTPException(
            status_code=400,
            detail=(
                "Code execution is not supported for this course language yet."
            ),
        )

    total_score = 0
    total_points = sum(
        questions[qid].points for qid in expected_ids
    )

    if total_points <= 0:
        raise HTTPException(
            status_code=400,
            detail="Invalid exam points configuration.",
        )

    try:
        # 4. Grade each question.
        for saved_answer in saved_answers:
            question = questions[saved_answer.question_id]

            question_type = question.question_type

            # Check whether the student answered this question.
            has_answer = any([
                saved_answer.answer_text
                and saved_answer.answer_text.strip(),

                saved_answer.submitted_code
                and saved_answer.submitted_code.strip(),

                saved_answer.audio_transcript
                and saved_answer.audio_transcript.strip(),
            ])

            # Unanswered questions receive zero points.
            if not has_answer:
                saved_answer.is_correct = False
                saved_answer.score = 0
                saved_answer.feedback = "Not answered."
                continue

            is_correct = False
            earned_points = 0
            feedback = "Incorrect answer."

            if question_type == "multiple_choice":
                try:
                    options = json.loads(
                        question.options_json or "[]"
                    )
                except (json.JSONDecodeError, TypeError):
                    raise HTTPException(
                        status_code=500,
                        detail="Invalid question options configuration.",
                    )

                valid_option_ids = {
                    str(option.get("id"))
                    for option in options
                    if isinstance(option, dict)
                    and option.get("id") is not None
                }

                selected = (
                    saved_answer.answer_text or ""
                ).strip()

                if selected in valid_option_ids:
                    is_correct = (
                        selected == (question.correct_answer or "").strip()
                    )

            elif question_type == "true_false":
                selected = (
                    saved_answer.answer_text or ""
                ).strip().lower()

                expected = (
                    question.correct_answer or ""
                ).strip().lower()

                if expected not in ("true", "false"):
                    raise HTTPException(
                        status_code=500,
                        detail="Invalid true/false question configuration.",
                    )

                if selected in ("true", "false"):
                    is_correct = selected == expected

            elif question_type in (
                "code_completion",
                "code_ordering",
                "find_bug",
                "problem_solving",
            ):
                code = saved_answer.submitted_code or ""

                if not code.strip():
                    is_correct = False
                    earned_points = 0
                    feedback = "No code was submitted."

                else:
                    if len(code) > 10000:
                        raise HTTPException(
                            status_code=413,
                            detail="Submitted code is too large.",
                        )

                    test_cases = db.query(
                        ExamQuestionTestCaseModel
                    ).filter(
                        ExamQuestionTestCaseModel.question_id == question.id
                    ).order_by(
                        ExamQuestionTestCaseModel.id
                    ).all()

                    if not test_cases:
                        raise HTTPException(
                            status_code=500,
                            detail="A code question has no test cases.",
                        )

                    if len(test_cases) > 20:
                        raise HTTPException(
                            status_code=400,
                            detail="A question has too many test cases.",
                        )

                    passed_case_points = 0

                    total_case_points = sum(
                        max(0, test_case.points)
                        for test_case in test_cases
                    )

                    if total_case_points <= 0:
                        raise HTTPException(
                            status_code=500,
                            detail="Invalid test case points configuration.",
                        )

                    all_cases_passed = True

                    for test_case in test_cases:
                        result = run_code_test_case(
                            code=code,
                            input_data=test_case.input_data,
                            language=language,
                        )

                        if result["status"] != "success":
                            all_cases_passed = False
                            break

                        actual_output = (
                            result["output"]
                            .replace("\r\n", "\n")
                            .strip()
                        )

                        expected_output = (
                            test_case.expected_output
                            .replace("\r\n", "\n")
                            .strip()
                        )

                        if actual_output == expected_output:
                            passed_case_points += max(
                                0, test_case.points
                            )
                        else:
                            all_cases_passed = False

                    earned_points = round(
                        question.points
                        * passed_case_points
                        / total_case_points
                    )

                    is_correct = (
                        all_cases_passed
                        and passed_case_points == total_case_points
                    )

                    feedback = (
                        "All tests passed."
                        if is_correct
                        else "Some tests did not pass."
                    )

            elif question_type == "voice_answer":
                student_text = " ".join(
                    (
                        saved_answer.audio_transcript
                        or saved_answer.answer_text
                        or ""
                    ).lower().split()
                )

                expected_text = " ".join(
                    (question.correct_answer or "").lower().split()
                )

                if not expected_text:
                    raise HTTPException(
                        status_code=500,
                        detail="Voice question has no expected answer.",
                    )

                is_correct = student_text == expected_text

           
          


                                    
            if question_type in (
                "multiple_choice",
                "true_false",
                "voice_answer",
            ):
                feedback = (
                    "Correct answer."
                    if is_correct
                    else "Incorrect answer."
                )
            # Award full points for correct theoretical answers.
            if question_type in (
                "multiple_choice",
                "true_false",
                "voice_answer",
            ):
                earned_points = (
                    question.points if is_correct else 0
                )

            earned_points = min(
                question.points,
                max(0, earned_points),
            )

            saved_answer.is_correct = is_correct
            saved_answer.score = earned_points
            saved_answer.feedback = feedback

            total_score += earned_points

        # 5. Calculate final result.
        percentage = round(
            (total_score / total_points) * 100,
            2,
        )

        passed = percentage >= exam.pass_score

        attempt.score = total_score
        attempt.total_points = total_points
        attempt.percentage = percentage
        attempt.passed = passed
        attempt.status = "submitted"
        attempt.submitted_at = datetime.utcnow()

        # 6. Advance the student only after passing.
        next_level = student_level.level
        course_completed = False

        if passed:
            if student_level.level == "beginner":
                next_level = "intermediate"

            elif student_level.level == "intermediate":
                next_level = "advanced"

            elif student_level.level == "advanced":
                student_level.is_completed = True
                course_completed = True

            if not course_completed:
                student_level.level = next_level

            student_level.updated_at = datetime.utcnow()

        db.commit()

        return {
            "message": (
                "Exam passed successfully."
                if passed
                else "Exam not passed. You can try again."
            ),
            "attempt_id": attempt.id,
            "exam_id": exam.id,
            "course_id": exam.course_id,
            "level": exam.level,
            "score": total_score,
            "total_points": total_points,
            "percentage": percentage,
            "pass_score": exam.pass_score,
            "passed": passed,
            "next_level": (
                "completed" if course_completed
                else next_level
            ),
            "course_completed": course_completed,
            "xp_awarded": 0,
        }

    except Exception:
        db.rollback()
        raise

# =================================
# Add Questions to Level Exams
# =================================

@app.post("/admin/exams/{exam_id}/questions")
def admin_add_exam_question(
    exam_id: int,
    payload: ExamQuestionCreateRequest,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_expert),
):
    exam = db.query(LevelExamModel).filter(
        LevelExamModel.id == exam_id
    ).first()

    if exam is None:
        raise HTTPException(
            status_code=404,
            detail="Exam not found"
        )

    allowed_types = [
        "multiple_choice",
        "true_false",
        "code_completion",
        "code_ordering",
        "find_bug",
        "problem_solving",
        "voice_answer",
    ]

    if payload.question_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail="Unsupported question type"
        )

    if payload.question_type == "multiple_choice":
        if not payload.options or not payload.correct_answer:
            raise HTTPException(
                status_code=400,
                detail="Multiple choice questions require options and a correct answer"
            )

        option_ids = [
            str(option.get("id", ""))
            for option in payload.options
        ]

        if (
            not all(option_ids)
            or len(option_ids) != len(set(option_ids))
            or payload.correct_answer not in option_ids
        ):
            raise HTTPException(
                status_code=400,
                detail="Invalid options or correct answer"
            )


    code_question_types = [
        "code_completion",
        "code_ordering",
        "find_bug",
        "problem_solving",
    ]


    if payload.question_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail="Invalid question type"
        )
    if (
        payload.question_type in code_question_types
        and not payload.test_cases
    ):
        raise HTTPException(
            status_code=400,
            detail="Code questions require at least one test case"
        )

    if payload.test_cases and len(payload.test_cases) > 20:
        raise HTTPException(
            status_code=400,
            detail="A question cannot have more than 20 test cases"
        )

    if payload.question_type == "voice_answer":
        if (
            not payload.correct_answer
            or not payload.correct_answer.strip()
        ):
            raise HTTPException(
                status_code=400,
                detail="Voice questions require a correct answer"
            )   

    
    if payload.question_type == "true_false":
        if (payload.correct_answer or "").strip().lower() not in (
            "true",
            "false",
        ):
            raise HTTPException(
                status_code=400,
                detail="True/False questions require 'true' or 'false' as the correct answer"
            )
    if (
        payload.question_type not in code_question_types
        and payload.test_cases
    ):
        raise HTTPException(
            status_code=400,
            detail="Test cases are only allowed for code questions"
        )
    if not payload.question_text or not payload.question_text.strip():
        raise HTTPException(
            status_code=400,
            detail="Question text cannot be empty"
        )

    if payload.points <= 0:
        raise HTTPException(
            status_code=400,
            detail="Question points must be greater than zero"
        )

    if payload.order_index < 0:
        raise HTTPException(
            status_code=400,
            detail="Question order cannot be negative"
        )
    question = ExamQuestionModel(
        exam_id=exam_id,
        question_type=payload.question_type,
        question_text=payload.question_text.strip(),
        options_json=(
            json.dumps(payload.options, ensure_ascii=False)
            if payload.options is not None
            else None
        ),
        correct_answer=payload.correct_answer,
        explanation=payload.explanation,
        starter_code=payload.starter_code,
        points=payload.points,
        order_index=payload.order_index,
        is_active=True,
    )

    db.add(question)
    db.flush()

    for test_case in payload.test_cases:

        if (
            not isinstance(test_case.input_data, str)
            or not isinstance(test_case.expected_output, str)
        ):
            raise HTTPException(
                status_code=400,
                detail="Test case input and expected output must be text"
            )

        if (
            not test_case.input_data.strip()
            and not test_case.expected_output.strip()
        ):
            raise HTTPException(
                status_code=400,
                detail="Test case input and expected output cannot both be empty"
            )

        exam_test_case = ExamQuestionTestCaseModel(
            question_id=question.id,
            input_data=test_case.input_data,
            expected_output=test_case.expected_output,
            is_hidden=test_case.is_hidden,
            points=1,
        )

       

        db.add(exam_test_case)

    db.commit()
    db.refresh(question)

    return {
        "message": "Exam question added successfully",
        "question_id": question.id,
        "exam_id": exam_id,
        "question_type": question.question_type,
        "points": question.points,
        "test_cases_count": len(payload.test_cases),
    }

@app.get("/exam-attempts/{attempt_id}/result")
def get_exam_attempt_result(
    attempt_id: int,
    current_user: UserModel = Depends(get_current_firebase_user),
    db: Session = Depends(get_db),
):
    # 1. التأكد أن المحاولة تخص الطالب الحالي
    attempt = db.query(ExamAttemptModel).filter(
        ExamAttemptModel.id == attempt_id,
        ExamAttemptModel.student_id == current_user.id
    ).first()

    if not attempt:
        raise HTTPException(
            status_code=404,
            detail="Exam attempt not found."
        )

    # 2. لا نعرض النتيجة قبل تسليم الامتحان
    if attempt.status != "submitted":
        raise HTTPException(
            status_code=400,
            detail="The exam has not been submitted yet."
        )

    # 3. جلب بيانات الامتحان
    exam = db.query(LevelExamModel).filter(
        LevelExamModel.id == attempt.exam_id
    ).first()

    if not exam:
        raise HTTPException(
            status_code=404,
            detail="Exam not found."
        )

    # 4. جلب إجابات الطالب
    saved_answers = db.query(ExamAnswerModel).filter(
        ExamAnswerModel.attempt_id == attempt.id
    ).all()

    questions_results = []

    for saved_answer in saved_answers:
        question = db.query(ExamQuestionModel).filter(
            ExamQuestionModel.id == saved_answer.question_id
        ).first()

        if not question:
            continue

        # تحويل الخيارات من JSON إلى قائمة
        try:
            options = (
                json.loads(question.options_json)
                if question.options_json
                else []
            )
        except (json.JSONDecodeError, TypeError):
            options = []

        questions_results.append({
            "question_id": question.id,
            "question_type": question.question_type,
            "question_text": question.question_text,
            "options": options,
            "order_index": question.order_index,
            "student_answer": saved_answer.answer_text,
            "submitted_code": saved_answer.submitted_code,
            "audio_transcript": saved_answer.audio_transcript,

            "is_correct": saved_answer.is_correct,
            "score": saved_answer.score,
            "max_points": question.points,
            "feedback": saved_answer.feedback,

            # تظهر بعد تسليم الامتحان فقط
            "correct_answer": question.correct_answer,
            "explanation": question.explanation,
        })

    # 5. ترتيب الأسئلة حسب ترتيبها في الامتحان
    questions_results.sort(
        key=lambda item: item["order_index"]
    )
    # 6. إرجاع النتيجة النهائية
    return {
        "success": True,
        "attempt_id": attempt.id,
        "exam_id": exam.id,
        "exam_title": exam.title,
        "level": exam.level,

        "score": attempt.score,
        "total_points": attempt.total_points,
        "percentage": attempt.percentage,
        "passed": attempt.passed,
        "pass_score": exam.pass_score,

        "status": attempt.status,
        "started_at": (
            attempt.started_at.isoformat()
            if attempt.started_at else None
        ),
        "submitted_at": (
            attempt.submitted_at.isoformat()
            if attempt.submitted_at else None
        ),

        "xp_awarded": 0,
        "questions": questions_results,
    }

@app.get("/courses/{course_id}/exam-history")
def get_student_exam_history(
    course_id: int,
    current_user: UserModel = Depends(get_current_firebase_user),
    db: Session = Depends(get_db),
):
    # 1. التأكد من وجود المساق
    course = db.query(CourseModel).filter(
        CourseModel.id == course_id
    ).first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found."
        )

    # 2. جلب محاولات الطالب لهذا المساق
    attempts = (
        db.query(ExamAttemptModel, LevelExamModel)
        .join(
            LevelExamModel,
            ExamAttemptModel.exam_id == LevelExamModel.id
        )
        .filter(
            ExamAttemptModel.student_id == current_user.id,
            LevelExamModel.course_id == course_id,
            ExamAttemptModel.status == "submitted"
        )
        .order_by(ExamAttemptModel.started_at.desc())
        .all()
    )

    # 3. تجهيز سجل المحاولات
    history = []

    for attempt, exam in attempts:
        history.append({
            "attempt_id": attempt.id,
            "exam_id": exam.id,
            "exam_title": exam.title,
            "level": exam.level,

            "score": attempt.score,
            "total_points": attempt.total_points,
            "percentage": attempt.percentage,

            "passed": attempt.passed,
            "pass_score": exam.pass_score,

            "status": attempt.status,

            "started_at": (
                attempt.started_at.isoformat()
                if attempt.started_at else None
            ),

            "submitted_at": (
                attempt.submitted_at.isoformat()
                if attempt.submitted_at else None
            ),
        })

    # 4. إرجاع السجل
    return {
        "success": True,
        "course_id": course_id,
        "course_title": course.title,
        "total_attempts": len(history),
        "attempts": history,
    }

# =================================
# Update Level Exam Question
# =================================

@app.put("/admin/exam-questions/{question_id}")
def admin_update_exam_question(
    question_id: int,
    payload: ExamQuestionCreateRequest,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_expert),
):
    question = db.query(ExamQuestionModel).filter(
        ExamQuestionModel.id == question_id
    ).first()

    if question is None:
        raise HTTPException(
            status_code=404,
            detail="Question not found"
        )

    has_attempts = db.query(ExamAttemptModel).filter(
        ExamAttemptModel.exam_id == question.exam_id
    ).first()

    if has_attempts:
        raise HTTPException(
            status_code=409,
            detail="Cannot modify questions after the first exam attempt"
        )

    code_question_types = [
        "code_completion",
        "code_ordering",
        "find_bug",
        "problem_solving",
    ]

    if (
        payload.question_type in code_question_types
        and not payload.test_cases
    ):
        raise HTTPException(
            status_code=400,
            detail="Code questions require at least one test case"
        )

    if payload.test_cases and len(payload.test_cases) > 20:
        raise HTTPException(
            status_code=400,
            detail="A question cannot have more than 20 test cases"
        )

    if payload.question_type == "true_false":
        if (payload.correct_answer or "").strip().lower() not in ("true", "false"):
            raise HTTPException(
                status_code=400,
                detail="True/False questions require 'true' or 'false' as the correct answer"
            )

    if payload.question_type == "voice_answer":
        if not payload.correct_answer or not payload.correct_answer.strip():
            raise HTTPException(
                status_code=400,
                detail="Voice questions require a correct answer"
            )

    if payload.question_type == "multiple_choice":
        if not payload.options or not payload.correct_answer:
            raise HTTPException(
                status_code=400,
                detail="Multiple choice questions require options and a correct answer"
            )

        option_ids = [
            str(option.get("id", ""))
            for option in payload.options
        ]

        if (
            not all(option_ids)
            or len(option_ids) != len(set(option_ids))
            or payload.correct_answer not in option_ids
        ):
            raise HTTPException(
                status_code=400,
                detail="Invalid options or correct answer"
            )

    if (
        payload.question_type not in code_question_types
        and payload.test_cases
    ):
        raise HTTPException(
            status_code=400,
            detail="Test cases are only allowed for code questions"
        )
    if not payload.question_text or not payload.question_text.strip():
        raise HTTPException(
            status_code=400,
            detail="Question text cannot be empty"
        )
    if payload.points <= 0:
        raise HTTPException(
            status_code=400,
            detail="Question points must be greater than zero"
        )    
    if payload.order_index < 0:
        raise HTTPException(
            status_code=400,
            detail="Question order cannot be negative"
        )            
    question.question_type = payload.question_type
    question.question_text = payload.question_text.strip()
    question.options_json = (
        json.dumps(payload.options, ensure_ascii=False)
        if payload.options is not None
        else None
    )
    question.correct_answer = payload.correct_answer
    question.explanation = payload.explanation
    question.starter_code = payload.starter_code
    question.points = payload.points
    question.order_index = payload.order_index

    # Validate all replacement test cases before changing existing database rows.
    for test_case in payload.test_cases:
        if (
            not isinstance(test_case.input_data, str)
            or not isinstance(test_case.expected_output, str)
        ):
            raise HTTPException(
                status_code=400,
                detail="Test case input and expected output must be text"
            )

        if (
            not test_case.input_data.strip()
            and not test_case.expected_output.strip()
        ):
            raise HTTPException(
                status_code=400,
                detail="Test case input and expected output cannot both be empty"
            )

    # Replace existing test cases only after validation succeeds.
    db.query(ExamQuestionTestCaseModel).filter(
        ExamQuestionTestCaseModel.question_id == question_id
    ).delete(synchronize_session=False)

    for test_case in payload.test_cases:
        db.add(
            ExamQuestionTestCaseModel(
                question_id=question_id,
                input_data=test_case.input_data,
                expected_output=test_case.expected_output,
                is_hidden=test_case.is_hidden,
                points=1,
            )
        )

    db.commit()
    db.refresh(question)

    return {
        "message": "Exam question updated successfully",
        "question_id": question.id,
        "question_type": question.question_type,
    }

# =================================
# Delete Level Exam Question
# =================================

@app.delete("/admin/exam-questions/{question_id}")
def admin_delete_exam_question(
    question_id: int,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_expert),
):
    question = db.query(ExamQuestionModel).filter(
        ExamQuestionModel.id == question_id
    ).first()

    if question is None:
        raise HTTPException(
            status_code=404,
            detail="Question not found"
        )

    has_attempts = db.query(ExamAttemptModel).filter(
        ExamAttemptModel.exam_id == question.exam_id
    ).first()

    if has_attempts:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete questions after the first exam attempt"
        )

    db.query(ExamQuestionTestCaseModel).filter(
        ExamQuestionTestCaseModel.question_id == question_id
    ).delete(synchronize_session=False)

    db.delete(question)
    db.commit()

    return {
        "message": "Exam question deleted successfully",
        "question_id": question_id
    }

# =================================
# 20. Real Python Code Execution
# Docker Sandbox
# =================================

@app.post("/code/run")
def run_code(
    payload: CodeRunRequest,
    current_user: UserModel = Depends(get_current_firebase_user),
):
    code = payload.code

    if not code.strip():
        raise HTTPException(
            status_code=400,
            detail="Code cannot be empty"
        )

    if len(code) > 10000:
        raise HTTPException(
            status_code=413,
            detail="Code is too large"
        )

    if payload.language_id == 1:
        result = run_python_test_case(
            code=code,
            input_data=""
        )

    elif payload.language_id == 2:
        result = run_java_test_case(
            code=code,
            input_data=""
        )

    elif payload.language_id == 3:
        result = run_javascript_test_case(
            code=code,
            input_data=""
        )

    else:
        raise HTTPException(
            status_code=400,
            detail="Unsupported language_id"
        )

    return result


# =================================
# 21. AI Programming Tutor - Free Local Version
# =================================

@app.post("/ai/tutor")
def ai_tutor(
    payload: AITutorRequest,
    current_user: UserModel = Depends(get_current_firebase_user),
):
    if not payload.message or not payload.message.strip():
        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty."
        )

    supported_languages = ["python", "java", "javascript", "cpp"]
    language = (payload.language or "").lower().strip()

    if language not in supported_languages:
        raise HTTPException(
            status_code=400,
            detail="Unsupported programming language."
        )

    message = payload.message.strip()
    code = (payload.code or "").strip()

    
    # ---------------------------------
    # Programming Knowledge Database
    # ---------------------------------

    knowledge = {
        "python": {
            "variable": {
                "title": "Variables in Python",
                "explanation": (
                    "A variable is a name used to store a value "
                    "so that we can use it later."
                ),
                "example": 'name = "Shahad"\nage = 20\nprint(name)\nprint(age)',
                "details": [
                    'name = "Shahad": Stores a string inside the name variable.',
                    "age = 20: Stores the number 20 inside the age variable.",
                    "print(name): Prints the value of the name variable.",
                    "print(age): Prints the value of the age variable."
                ]
            },
            "condition": {
                "title": "Conditions in Python",
                "explanation": (
                    "We use if to make decisions based on whether a condition is true."
                ),
                "example": (
                    "age = 18\n"
                    "if age >= 18:\n"
                    '    print("Adult")\n'
                    "else:\n"
                    '    print("Young")'
                ),
                "details": [
                    "age = 18: Stores the age.",
                    "if age >= 18: Checks the condition.",
                    "print: Executes the command if the condition is true.",
                    "else: Executes the alternative if the condition is false."
                ]
            },
            "loop": {
                "title": "Loops in Python",
                "explanation": "Loops are used to repeat the execution of commands.",
                "example": (
                    "for i in range(5):\n"
                    "    print(i)"
                ),
                "details": [
                    "for: Starts a loop.",
                    "i: A variable representing the current iteration value.",
                    "range(5): Repeats the loop five times.",
                    "print(i): Prints the value of i in each iteration."
                ]
            },
            "function": {
                "title": "Functions in Python",
                "explanation": "A function is a group of commands placed under a specific name so they can be reused.",
                "example": (
                    "def greet(name):\n"
                    '    print("Hello", name)\n\n'
                    'greet("Ali")'
                ),
                "details": [
                    "def: A keyword used to define a function.",
                    "greet: The function name.",
                    "name: A parameter received by the function.",
                    "print: Displays the greeting message.",
                    'greet("Ali"): Calls the function.'
                ]
            }
        },

        "java": {
            "variable": {
                "title": "Variables in Java",
                "explanation": "A variable is a storage location for a value of a specific data type.",
                "example": (
                    'String name = "Ali";\n'
                    "int age = 20;\n"
                    "System.out.println(name);\n"
                    "System.out.println(age);"
                ),
                "details": [
                    "String: A data type used for text.",
                    "int: A data type used for whole numbers.",
                    "name and age: Variable names.",
                    "System.out.println: Prints the values."
                ]
            },
            "condition": {
                "title": "Conditions in Java",
                "explanation": "The if and else statements are used to make decisions.",
                "example": (
                    "int age = 18;\n"
                    "if (age >= 18) {\n"
                    '    System.out.println("Adult");\n'
                    "} else {\n"
                    '    System.out.println("Young");\n'
                    "}"
                ),
                "details": [
                    "int age = 18: Defines an age variable.",
                    "if: Checks the condition.",
                    "System.out.println: Prints the result.",
                    "else: Executes the alternative."
                ]
            },
            "loop": {
                "title": "Loops in Java",
                "explanation": "Loops allow commands to be repeated.",
                "example": (
                    "for (int i = 0; i < 5; i++) {\n"
                    "    System.out.println(i);\n"
                    "}"
                ),
                "details": [
                    "int i = 0: Initializes the counter.",
                    "i < 5: The condition for continuing the loop.",
                    "i++: Increases the counter by one.",
                    "System.out.println: Prints the counter value."
                ]
            },
            "function": {
                "title": "Functions in Java",
                "explanation": "Functions are used to organize commands and reuse code.",
                "example": (
                    "public static void greet() {\n"
                    '    System.out.println("Hello");\n'
                    "}\n\n"
                    "greet();"
                ),
                "details": [
                    "public static: Access and method modifiers.",
                    "void: Indicates that the method does not return a value.",
                    "greet: The method name.",
                    "greet(): Calls the method."
                ]
            }
        },

        "javascript": {
            "variable": {
                "title": "Variables in JavaScript",
                "explanation": "A variable stores a value that can be used later.",
                "example": (
                    'let name = "Ali";\n'
                    "let age = 20;\n"
                    "console.log(name);\n"
                    "console.log(age);"
                ),
                "details": [
                    "let: Defines a variable whose value can be changed.",
                    "name: The variable name.",
                    "console.log: Prints the value."
                ]
            },
            "condition": {
                "title": "Conditions in JavaScript",
                "explanation": "We use if and else to make decisions.",
                "example": (
                    "let age = 18;\n"
                    "if (age >= 18) {\n"
                    '    console.log("Adult");\n'
                    "} else {\n"
                    '    console.log("Young");\n'
                    "}"
                ),
                "details": [
                    "let age = 18: Defines the age variable.",
                    "if: Checks the condition.",
                    "console.log: Displays the result.",
                    "else: Executes the alternative."
                ]
            },
            "loop": {
                "title": "Loops in JavaScript",
                "explanation": "Loops are used to repeat the execution of commands.",
                "example": (
                    "for (let i = 0; i < 5; i++) {\n"
                    "    console.log(i);\n"
                    "}"
                ),
                "details": [
                    "let i = 0: Initializes the counter.",
                    "i < 5: The condition for continuing the loop.",
                    "i++: Increases the counter.",
                    "console.log: Prints the value."
                ]
            },
            "function": {
                "title": "Functions in JavaScript",
                "explanation": "A function is a group of reusable commands.",
                "example": (
                    "function greet(name) {\n"
                    '    console.log("Hello " + name);\n'
                    "}\n\n"
                    'greet("Ali");'
                ),
                "details": [
                    "function: Defines a function.",
                    "greet: The function name.",
                    "name: A parameter received by the function.",
                    "greet('Ali'): Calls the function."
                ]
            }
        },

        "cpp": {
            "variable": {
                "title": "Variables in C++",
                "explanation": "A variable stores a value of a specific data type.",
                "example": (
                    '#include <iostream>\n'
                    "using namespace std;\n"
                    "int main() {\n"
                    '    string name = "Ali";\n'
                    "    int age = 20;\n"
                    "    cout << name << endl;\n"
                    "    cout << age << endl;\n"
                    "    return 0;\n"
                    "}"
                ),
                "details": [
                    "#include <iostream>: Includes the input and output library.",
                    "using namespace std: Uses the standard namespace.",
                    "int main(): The starting point of the program.",
                    "string: A data type used for text.",
                    "int: A data type used for whole numbers.",
                    "cout: Prints values.",
                    "return 0: Ends the program successfully."
                ]
            },
            "condition": {
                "title": "Conditions in C++",
                "explanation": "The if and else statements are used to execute different decisions.",
                "example": (
                    '#include <iostream>\n'
                    "using namespace std;\n"
                    "int main() {\n"
                    "    int age = 18;\n"
                    "    if (age >= 18) {\n"
                    '        cout << "Adult";\n'
                    "    } else {\n"
                    '        cout << "Young";\n'
                    "    }\n"
                    "    return 0;\n"
                    "}"
                ),
                "details": [
                    "int age = 18: Defines an age variable.",
                    "if: Checks the condition.",
                    "cout: Prints the result.",
                    "else: Executes the alternative."
                ]
            },
            "loop": {
                "title": "Loops in C++",
                "explanation": "Loops are used to repeat commands.",
                "example": (
                    '#include <iostream>\n'
                    "using namespace std;\n"
                    "int main() {\n"
                    "    for (int i = 0; i < 5; i++) {\n"
                    "        cout << i << endl;\n"
                    "    }\n"
                    "    return 0;\n"
                    "}"
                ),
                "details": [
                    "for: Starts the loop.",
                    "int i = 0: Defines the counter.",
                    "i < 5: The condition for continuing the loop.",
                    "i++: Increases the counter.",
                    "cout: Prints the value."
                ]
            },
            "function": {
                "title": "Functions in C++",
                "explanation": "A function is a group of commands with a name that can be called when needed.",
                "example": (
                    '#include <iostream>\n'
                    "using namespace std;\n"
                    "void greet() {\n"
                    '    cout << "Hello";\n'
                    "}\n"
                    "int main() {\n"
                    "    greet();\n"
                    "    return 0;\n"
                    "}"
                ),
                "details": [
                    "void: Defines a function that does not return a value.",
                    "greet: The function name.",
                    "cout: Prints a message.",
                    "greet(): Calls the function."
                ]
            }
        }
    }

    # ---------------------------------
    # Identify the requested topic
    # ---------------------------------

    topics = {
        "variable": [
            "variable", "variables"
        ],
        "condition": [
            "condition", "conditions", "if", "else"
        ],
        "loop": [
            "loop", "loops", "for", "while"
        ],
        "function": [
            "function", "functions"
        ]
    }

    normalized_message = message.lower()

    selected_topic = None

    for topic, keywords in topics.items():
        if any(keyword in normalized_message for keyword in keywords):
            selected_topic = topic
            break

    # ---------------------------------
    # Generate a Local Educational Answer
    # ---------------------------------

    if selected_topic and not code:
        lesson = knowledge[language][selected_topic]

        answer = (
            f"## {lesson['title']}\n\n"
            f"{lesson['explanation']}\n\n"
            f"### Practical Example\n"
            f"```{language}\n{lesson['example']}\n```\n\n"
            "### Line-by-Line Code Explanation\n"
        )

        for index, detail in enumerate(lesson["details"], start=1):
            answer += f"\n{index}. {detail}"

        answer += (
            "\n\n### Tip\n"
            "Try to write the example by yourself and change the values "
            "so you can understand how it works."
        )

    elif code:
        answer = (
            "I received the code you sent.\n\n"
            "The current local assistant can provide explanations "
            "for basic programming concepts, but it does not have "
            "an AI model to analyze arbitrary code.\n\n"
            "You can run your code using the CodeQuest code editor. "
            "We will develop a local code analyzer for errors in the future."
        )

    else:
        answer = (
            "Welcome to the CodeQuest Learning Assistant!\n\n"
            "I can currently explain the following basic topics:\n"
            "- Variables\n"
            "- Conditions\n"
            "- Loops\n"
            "- Functions\n\n"
            f"Current programming language: {language}\n\n"
            "Enter the topic you want to learn."
        )

    return {
        "success": True,
        "answer": answer,
        "language": language,
        "provider": "local_free"
    }
