import pandas as pd
import numpy as np

source = r"C:\Users\mussa\Downloads\academic_ai_students_150.csv"
target = r"data\processed\DSstudents_processed.csv"

df = pd.read_csv(source)

assert len(df) == 150, "Expected 150 students"
assert "student_id" in df.columns
assert "roll_on_number" not in df.columns

# Map existing ML feature names
df["missing_submissions"] = df["missing_submission_assignment"]
df["attendance_change"] = df["change_in_attendance"]

total = df["assignments_completed"] + df["missing_submissions"]
df["assignment_submission_rate"] = np.where(
    total > 0,
    df["assignments_completed"] / total,
    0.0
)

# Synthetic activity proxy, NOT measured LMS activity.
df["activity_per_week"] = (
    2 + 0.06 * df["attendance"]
    + 0.4 * df["assignments_completed"]
).clip(2, 12).round(2)

# Required provenance marker
df["data_origin"] = "DEMO DATA"

# Compatibility aliases
df["previous_gpa"] = df["previous_cgpa"]
df["current_gpa"] = df["current_cgpa"]

# Validate ML features
required = [
    "student_id", "data_origin", "attendance",
    "assignments_completed", "activity_per_week",
    "missing_submissions", "attendance_change",
    "assignment_submission_rate"
]

assert all(c in df.columns for c in required)
assert df["student_id"].is_unique
assert df["data_origin"].eq("DEMO DATA").all()

df.to_csv(target, index=False, encoding="utf-8-sig")

print("Dataset replaced successfully!")
print("Students:", len(df))
print("Columns:", len(df.columns))
print("Semester:", df["semester"].unique())
print("Output:", target)
