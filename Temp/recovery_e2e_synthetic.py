from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from getpass import getpass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_PATH = Path(
    r"C:\Users\mussa\academic-ai-for-impact\Temp\recovery_e2e_synthetic.sqlite"
).resolve()
SHARED_PATH = Path(
    r"C:\Users\mussa\academic-ai-for-impact\backend\data\academic_ai.db"
).resolve()

EXPECTED_REVISION = "m4n5o6p7q8r9"
EXPECTED_POLICY_SCORE = 12
EXPECTED_POLICY_MAXIMUM = 25
TEST_CODES = ("SYN-REC-LOW", "SYN-REC-12")
TEST_EMAILS = (
    "recovery.teacher@example.com",
    "recovery.student@example.com",
)
TEST_STUDENT_ID = "SYNTH-RECOVERY-001"
TEST_COURSE_CODES = ("SYN-REC-LOW-01", "SYN-REC-12-01")
TEST_ASSESSMENT_TITLES = (
    "SYNTHETIC FA-1 below threshold",
    "SYNTHETIC FA-1 exactly 12",
)
INSTANCE_MUTEX_NAME = r"Global\AcademicAIForImpactRecoveryE2ESyntheticSeeder"


class WindowsNamedMutex:
    def __init__(self) -> None:
        self._kernel32: Any = None
        self._handle: Any = None
        self._owns_mutex = False

    def acquire(self) -> None:
        if os.name != "nt":
            raise RuntimeError("A Windows named mutex is required; refusing to proceed.")

        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        kernel32.ReleaseMutex.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        self._kernel32 = kernel32

        handle = kernel32.CreateMutexW(None, False, INSTANCE_MUTEX_NAME)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = handle

        try:
            wait_result = kernel32.WaitForSingleObject(handle, 0)
        except BaseException:
            self._release_after_acquisition_error()
            raise

        if wait_result == 0:
            self._owns_mutex = True
            return
        if wait_result == 0x80:
            self._owns_mutex = True
            cleanup_errors = self.release()
            if cleanup_errors:
                raise RuntimeError(
                    "An abandoned seeder mutex could not be released cleanly; refusing to proceed."
                ) from cleanup_errors[0]
            raise RuntimeError(
                "A prior seeder terminated while holding the mutex; refusing to proceed."
            )

        error_code = ctypes.get_last_error() if wait_result == 0xFFFFFFFF else None
        close_error = self._close_handle()
        if close_error is not None:
            raise RuntimeError(
                "Could not close the seeder mutex handle after acquisition failed."
            ) from close_error
        if wait_result == 0x102:
            raise RuntimeError("Another seeder instance is already running.")
        if wait_result == 0xFFFFFFFF:
            raise ctypes.WinError(error_code)
        raise RuntimeError("Could not establish exclusive seeder execution.")

    def _close_handle(self) -> BaseException | None:
        if self._handle is None or self._kernel32 is None:
            return None
        if not self._kernel32.CloseHandle(self._handle):
            import ctypes

            return ctypes.WinError(ctypes.get_last_error())
        self._handle = None
        return None

    def _release_after_acquisition_error(self) -> None:
        if self._handle is None or self._kernel32 is None:
            return
        try:
            self._kernel32.ReleaseMutex(self._handle)
        finally:
            self._owns_mutex = False
            self._close_handle()

    def release(self) -> list[BaseException]:
        errors: list[BaseException] = []
        if self._handle is None or self._kernel32 is None:
            return errors

        if self._owns_mutex:
            try:
                if self._kernel32.ReleaseMutex(self._handle):
                    self._owns_mutex = False
                else:
                    import ctypes

                    errors.append(ctypes.WinError(ctypes.get_last_error()))
            except BaseException as exc:
                errors.append(exc)

        try:
            close_error = self._close_handle()
            if close_error is not None:
                errors.append(close_error)
        except BaseException as exc:
            errors.append(exc)
        return errors


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def check_no_sidecars() -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(TEST_PATH) + suffix)
        require(
            not sidecar.exists() and not sidecar.is_symlink(),
            f"Refusing: SQLite {suffix} sidecar exists for the test database.",
        )


def check_file_isolation() -> None:
    require(
        TEST_PATH != SHARED_PATH,
        "Refusing: canonical test path equals the shared database.",
    )
    require(
        TEST_PATH.is_file(),
        "Refusing: the approved test database does not exist; this script will not create it.",
    )
    require(
        SHARED_PATH.is_file(),
        "Refusing: the expected shared database file does not exist.",
    )
    require(
        not os.path.samefile(TEST_PATH, SHARED_PATH),
        "Refusing: test and shared paths refer to the same file.",
    )
    check_no_sidecars()


def print_sanitized_diagnostic(label: str, exc: BaseException) -> None:
    exc_type = type(exc)
    print(
        f"{label} ({exc_type.__module__}.{exc_type.__qualname__}).",
        file=sys.stderr,
    )
    print("Sanitized traceback locations:", file=sys.stderr)
    for frame in traceback.extract_tb(exc.__traceback__):
        print(
            f"  {Path(frame.filename).name}:{frame.lineno} in {frame.name}",
            file=sys.stderr,
        )


def verify_connection_and_revision(session: Any) -> None:
    from sqlalchemy import text

    attached = session.connection().exec_driver_sql(
        "PRAGMA database_list"
    ).one()
    attached_path = Path(attached[2]).resolve()
    require(
        attached_path == TEST_PATH,
        "Refusing: SQLite connection is not attached to the approved test database.",
    )
    require(
        not os.path.samefile(attached_path, SHARED_PATH),
        "Refusing: SQLite connection aliases the shared database.",
    )

    revision = session.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one()
    require(
        revision == EXPECTED_REVISION,
        f"Refusing: expected Alembic revision {EXPECTED_REVISION}.",
    )


def verify_latest_policy(session: Any, SubjectFocusPolicy: Any) -> None:
    policy = (
        session.query(SubjectFocusPolicy)
        .order_by(SubjectFocusPolicy.version.desc())
        .first()
    )
    require(
        policy is not None
        and policy.threshold_score == EXPECTED_POLICY_SCORE
        and policy.threshold_maximum == EXPECTED_POLICY_MAXIMUM,
        "Refusing: the latest subject-focus policy is not 12/25.",
    )


def verify_empty_preconditions(session: Any, models: dict[str, Any]) -> None:
    verify_connection_and_revision(session)
    verify_latest_policy(session, models["SubjectFocusPolicy"])

    for model_name in ("User", "Student", "Teacher", "Course", "Assessment", "Mark"):
        model = models[model_name]
        require(
            session.query(model).count() == 0,
            f"Refusing: the {model_name} table is not empty.",
        )

    Subject = models["Subject"]
    Course = models["Course"]
    User = models["User"]
    Student = models["Student"]
    require(
        session.query(Subject).filter(Subject.code.in_(TEST_CODES)).count() == 0,
        "Refusing: a synthetic subject code already exists.",
    )
    require(
        session.query(Course).filter(
            Course.code.in_(TEST_COURSE_CODES)
        ).count() == 0,
        "Refusing: a synthetic course code already exists.",
    )
    require(
        session.query(User).filter(User.email.in_(TEST_EMAILS)).count() == 0,
        "Refusing: a synthetic login already exists.",
    )
    require(
        session.query(Student).filter_by(student_id=TEST_STUDENT_ID).count() == 0,
        "Refusing: the synthetic student identifier already exists.",
    )


def assert_eligibility_results(
    focus: dict[str, Any],
    student_id: int,
) -> None:
    focus_by_code = {
        subject_row["code"]: subject_row
        for subject_row in focus["subjects"]
    }
    require(
        set(TEST_CODES).issubset(focus_by_code),
        "Eligibility assertion failed: one or more expected subjects are missing.",
    )

    low_row = next(
        row
        for row in focus_by_code[TEST_CODES[0]]["students"]
        if row["id"] == student_id
    )
    boundary_row = next(
        row
        for row in focus_by_code[TEST_CODES[1]]["students"]
        if row["id"] == student_id
    )

    require(
        low_row["status"] == "LOW"
        and "LOW_SCORE" in low_row["support_triggers"]
        and low_row["latest_assessment"] is not None
        and low_row["latest_assessment"]["normalized_score"] == 10.0
        and low_row["latest_assessment"]["maximum_marks"] == 25.0
        and low_row["latest_assessment"]["official_result"] is None,
        "Eligibility assertion failed: synthetic 10/25 case was not LOW.",
    )
    require(
        boundary_row["status"] == "NOT_LOW"
        and "LOW_SCORE" not in boundary_row["support_triggers"]
        and boundary_row["latest_assessment"] is not None
        and boundary_row["latest_assessment"]["normalized_score"] == 12.0
        and boundary_row["latest_assessment"]["maximum_marks"] == 25.0
        and boundary_row["latest_assessment"]["official_result"] is None,
        "Eligibility assertion failed: synthetic 12/25 case was not NOT_LOW.",
    )


def attempt_rollback(session: Any, transaction: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "attempted": True,
        "call_succeeded": False,
        "session_inactive": None,
        "transaction_inactive": None,
        "error": None,
        "interrupted": False,
        "state_uncertain": True,
    }
    try:
        session.rollback()
        result["call_succeeded"] = True
    except BaseException as exc:
        result["error"] = exc
        result["interrupted"] = isinstance(exc, (KeyboardInterrupt, SystemExit))

    try:
        result["session_inactive"] = not session.in_transaction()
        result["transaction_inactive"] = (
            None if transaction is None else not transaction.is_active
        )
        result["state_uncertain"] = not (
            result["call_succeeded"]
            and result["session_inactive"] is True
            and result["transaction_inactive"] is not False
        )
    except BaseException as exc:
        if result["error"] is None:
            result["error"] = exc
        result["interrupted"] = result["interrupted"] or isinstance(
            exc,
            (KeyboardInterrupt, SystemExit),
        )
        result["state_uncertain"] = True
    return result


def close_session(label: str, session: Any, errors: list[tuple[str, BaseException]]) -> bool:
    if session is None:
        return False
    try:
        session.close()
        return True
    except BaseException as exc:
        errors.append((label, exc))
        return False


def inspect_exact_synthetic_rows(session: Any, models: dict[str, Any]) -> dict[str, int]:
    User = models["User"]
    Student = models["Student"]
    Teacher = models["Teacher"]
    Subject = models["Subject"]
    Course = models["Course"]
    Assessment = models["Assessment"]
    Mark = models["Mark"]

    return {
        "users": session.query(User).filter(User.email.in_(TEST_EMAILS)).count(),
        "students": session.query(Student).filter(
            Student.student_id == TEST_STUDENT_ID
        ).count(),
        "teachers": (
            session.query(Teacher)
            .join(User, Teacher.user_id == User.id)
            .filter(User.email == TEST_EMAILS[0])
            .count()
        ),
        "subjects": session.query(Subject).filter(
            Subject.code.in_(TEST_CODES)
        ).count(),
        "courses": session.query(Course).filter(
            Course.code.in_(TEST_COURSE_CODES)
        ).count(),
        "assessments": session.query(Assessment).filter(
            Assessment.title.in_(TEST_ASSESSMENT_TITLES)
        ).count(),
        "marks": (
            session.query(Mark)
            .join(Student, Mark.student_id == Student.id)
            .filter(Student.student_id == TEST_STUDENT_ID)
            .count()
        ),
    }


def verify_absence_read_only(
    create_engine: Any,
    event: Any,
    URL: Any,
    sessionmaker: Any,
    text: Any,
    models: dict[str, Any],
) -> dict[str, int]:
    readonly_url = URL.create(
        "sqlite+pysqlite",
        database=TEST_PATH.as_uri(),
        query={"mode": "ro", "uri": "true"},
    )
    readonly_engine = create_engine(readonly_url)
    readonly_session = None
    errors: list[tuple[str, BaseException]] = []
    try:
        @event.listens_for(readonly_engine, "connect")
        def enforce_read_only(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA query_only=ON")
            cursor.close()

        ReadOnlySession = sessionmaker(
            bind=readonly_engine,
            autoflush=False,
            autocommit=False,
        )
        readonly_session = ReadOnlySession()
        attached = readonly_session.connection().exec_driver_sql(
            "PRAGMA database_list"
        ).one()
        attached_path = Path(attached[2]).resolve()
        require(
            attached_path == TEST_PATH
            and not os.path.samefile(attached_path, SHARED_PATH),
            "Absence check refused: read-only connection path is not isolated.",
        )
        query_only = readonly_session.execute(
            text("PRAGMA query_only")
        ).scalar_one()
        require(
            query_only == 1,
            "Absence check refused: SQLite query_only could not be verified.",
        )
        verify_connection_and_revision(readonly_session)
        verify_latest_policy(
            readonly_session,
            models["SubjectFocusPolicy"],
        )
        counts = inspect_exact_synthetic_rows(readonly_session, models)
    finally:
        session_closed = close_session(
            "read-only absence-check session close",
            readonly_session,
            errors,
        )
        try:
            readonly_engine.dispose()
        except BaseException as exc:
            errors.append(("read-only absence-check engine disposal", exc))

    if errors:
        label, exc = errors[0]
        raise RuntimeError(
            f"{label} failed; absence result is UNKNOWN."
        ) from exc
    require(session_closed, "Read-only absence-check session did not close.")
    return counts


def main() -> int:
    stage = "filesystem isolation preflight"
    failure: BaseException | None = None
    failure_stage: str | None = None
    cleanup_errors: list[tuple[str, BaseException]] = []
    interruption = False
    models: dict[str, Any] = {}

    engine = None
    application_engine = None
    Session = None
    db = None
    transaction = None
    post_db = None
    commit_attempted = False
    commit_confirmed = False
    preconditions_passed = False
    precommit_eligibility_passed = False
    post_commit_verification_passed = False
    insertion_session_closed = False
    post_commit_session_closed = False
    engine_disposed = False
    application_engine_disposed = False
    final_sidecar_check_passed = False
    mutex_released = False
    instance_mutex = WindowsNamedMutex()
    rollback_result: dict[str, Any] | None = None
    row_absence_status = "UNKNOWN"
    row_absence_counts: dict[str, int] | None = None

    try:
        stage = "acquiring single-instance Windows mutex"
        instance_mutex.acquire()

        check_file_isolation()

        sys.path.insert(0, str(PROJECT_ROOT))
        os.environ["DATABASE_URL"] = (
            f"sqlite:///{TEST_PATH.as_posix()}"
        )

        stage = "loading project models and services"
        from sqlalchemy import create_engine, event, text
        from sqlalchemy.engine import URL, make_url
        from sqlalchemy.orm import sessionmaker

        from backend.app.database import engine as application_engine
        from backend.app.models import (
            Assessment,
            Course,
            Mark,
            Student,
            Subject,
            SubjectFocusPolicy,
            Teacher,
            User,
        )
        from backend.app.services.auth import hash_password
        from backend.app.services.subject_focus import (
            _FIRST_INTERNAL_ASSESSMENT,
            build_teacher_subject_focus,
        )

        require(
            all(
                _FIRST_INTERNAL_ASSESSMENT.search(title)
                for title in TEST_ASSESSMENT_TITLES
            ),
            "Internal seeder check failed: a synthetic assessment title does not match FA-1.",
        )

        models = {
            "Assessment": Assessment,
            "Course": Course,
            "Mark": Mark,
            "Student": Student,
            "Subject": Subject,
            "SubjectFocusPolicy": SubjectFocusPolicy,
            "Teacher": Teacher,
            "User": User,
        }
        app_database = make_url(str(application_engine.url)).database
        require(
            app_database is not None
            and Path(app_database).resolve() == TEST_PATH,
            "Refusing: application default engine is not configured for the isolated test path.",
        )

        stage = "hidden synthetic password prompts"
        teacher_password = getpass("Choose synthetic teacher password (hidden): ")
        teacher_confirmation = getpass("Confirm synthetic teacher password (hidden): ")
        student_password = getpass("Choose synthetic student password (hidden): ")
        student_confirmation = getpass("Confirm synthetic student password (hidden): ")
        require(
            teacher_password
            and len(teacher_password) <= 256
            and teacher_password == teacher_confirmation,
            "Teacher password is empty, too long, or did not match.",
        )
        require(
            student_password
            and len(student_password) <= 256
            and student_password == student_confirmation,
            "Student password is empty, too long, or did not match.",
        )

        teacher_hash = hash_password(teacher_password)
        student_hash = hash_password(student_password)
        del teacher_password, teacher_confirmation
        del student_password, student_confirmation

        stage = "opening explicit read-write test database"
        rw_url = URL.create(
            "sqlite+pysqlite",
            database=TEST_PATH.as_uri(),
            query={"mode": "rw", "uri": "true"},
        )
        engine = create_engine(rw_url)

        @event.listens_for(engine, "connect")
        def enable_sqlite_foreign_keys(
            dbapi_connection: Any,
            _record: Any,
        ) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Session = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
        )

        stage = "database, migration, policy, and empty-record preconditions"
        with Session() as preflight_session:
            verify_empty_preconditions(preflight_session, models)
        preconditions_passed = True

        stage = "opening insertion session"
        db = Session()

        stage = "opening the single insertion transaction"
        transaction = db.begin()

        stage = "insertion transaction preconditions"
        verify_empty_preconditions(db, models)

        stage = "inserting synthetic records"
        teacher_user = User(
            email=TEST_EMAILS[0],
            full_name="Synthetic Recovery Teacher",
            role="TEACHER",
            password_hash=teacher_hash,
        )
        student_user = User(
            email=TEST_EMAILS[1],
            full_name="Synthetic Recovery Student",
            role="STUDENT",
            password_hash=student_hash,
        )
        db.add_all([teacher_user, student_user])
        db.flush()
        del teacher_hash, student_hash

        teacher = Teacher(
            user_id=teacher_user.id,
            department="Synthetic Test Only",
        )
        student = Student(
            user_id=student_user.id,
            student_id=TEST_STUDENT_ID,
            full_name="Synthetic Recovery Student",
            email=TEST_EMAILS[1],
            program="Synthetic Test Only",
            section="SYNTH-REC",
            department="Synthetic Test Only",
        )
        db.add_all([teacher, student])
        db.flush()

        low_subject = Subject(
            name="Synthetic Recovery Below Threshold",
            code=TEST_CODES[0],
            department="Synthetic Test Only",
        )
        boundary_subject = Subject(
            name="Synthetic Recovery At Threshold",
            code=TEST_CODES[1],
            department="Synthetic Test Only",
        )
        db.add_all([low_subject, boundary_subject])
        db.flush()

        low_course = Course(
            name="Synthetic Recovery Low Course",
            code=TEST_COURSE_CODES[0],
            section="SYNTH-REC",
            subject_id=low_subject.id,
            teacher_id=teacher.id,
        )
        boundary_course = Course(
            name="Synthetic Recovery Boundary Course",
            code=TEST_COURSE_CODES[1],
            section="SYNTH-REC",
            subject_id=boundary_subject.id,
            teacher_id=teacher.id,
        )
        db.add_all([low_course, boundary_course])
        db.flush()

        low_assessment = Assessment(
            course_id=low_course.id,
            title=TEST_ASSESSMENT_TITLES[0],
            maximum_marks=25,
            maximum_marks_verified=True,
        )
        boundary_assessment = Assessment(
            course_id=boundary_course.id,
            title=TEST_ASSESSMENT_TITLES[1],
            maximum_marks=25,
            maximum_marks_verified=True,
        )
        db.add_all([low_assessment, boundary_assessment])
        db.flush()

        recorded_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add_all(
            [
                Mark(
                    student_id=student.id,
                    assessment_id=low_assessment.id,
                    score=10,
                    recorded_at=recorded_at - timedelta(seconds=1),
                ),
                Mark(
                    student_id=student.id,
                    assessment_id=boundary_assessment.id,
                    score=12,
                    recorded_at=recorded_at,
                ),
            ]
        )
        db.flush()

        stage = "pre-commit evaluator assertions"
        precommit_focus = build_teacher_subject_focus(
            db,
            teacher_user,
            student_id=student.id,
            first_internal_only=True,
        )
        assert_eligibility_results(precommit_focus, student.id)
        precommit_eligibility_passed = True

        stage = "commit"
        commit_attempted = True
        transaction.commit()
        commit_confirmed = True

        stage = "post-commit persistence and evaluator verification"
        post_db = Session()
        verify_connection_and_revision(post_db)
        verify_latest_policy(post_db, SubjectFocusPolicy)
        persisted_teacher = post_db.query(User).filter_by(
            email=TEST_EMAILS[0],
            role="TEACHER",
        ).one()
        persisted_student_user = post_db.query(User).filter_by(
            email=TEST_EMAILS[1],
            role="STUDENT",
        ).one()
        persisted_student = post_db.query(Student).filter_by(
            student_id=TEST_STUDENT_ID,
            user_id=persisted_student_user.id,
        ).one()
        postcommit_focus = build_teacher_subject_focus(
            post_db,
            persisted_teacher,
            student_id=persisted_student.id,
            first_internal_only=True,
        )
        assert_eligibility_results(postcommit_focus, persisted_student.id)
        post_commit_verification_passed = True

    except BaseException as exc:
        failure = exc
        failure_stage = stage
        interruption = isinstance(exc, (KeyboardInterrupt, SystemExit))

    finally:
        try:
            if db is not None and not commit_attempted:
                rollback_result = attempt_rollback(db, transaction)
                interruption = interruption or rollback_result["interrupted"]

            insertion_session_closed = close_session(
                "insertion session close",
                db,
                cleanup_errors,
            )
            post_commit_session_closed = close_session(
                "post-commit session close",
                post_db,
                cleanup_errors,
            )

            if failure is not None and not commit_attempted:
                if db is not None and insertion_session_closed:
                    try:
                        row_absence_counts = verify_absence_read_only(
                            create_engine,
                            event,
                            URL,
                            sessionmaker,
                            text,
                            models,
                        )
                        row_absence_status = (
                            "VERIFIED_ABSENT"
                            if not any(row_absence_counts.values())
                            else "SYNTHETIC_ROWS_PRESENT"
                        )
                    except BaseException as exc:
                        cleanup_errors.append(("read-only row-absence inspection", exc))
                        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                            interruption = True
                        row_absence_status = "UNKNOWN"

            if engine is not None:
                try:
                    engine.dispose()
                    engine_disposed = True
                except BaseException as exc:
                    cleanup_errors.append(("insertion engine disposal", exc))
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        interruption = True

            if application_engine is not None:
                try:
                    application_engine.dispose()
                    application_engine_disposed = True
                except BaseException as exc:
                    cleanup_errors.append(("application engine disposal", exc))
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        interruption = True

            try:
                check_no_sidecars()
                final_sidecar_check_passed = True
            except BaseException as exc:
                cleanup_errors.append(("final SQLite sidecar check", exc))
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    interruption = True
        finally:
            try:
                mutex_errors = instance_mutex.release()
            except BaseException as exc:
                mutex_errors = [exc]
            if mutex_errors:
                cleanup_errors.extend(
                    ("single-instance mutex release", exc)
                    for exc in mutex_errors
                )
                if any(
                    isinstance(exc, (KeyboardInterrupt, SystemExit))
                    for exc in mutex_errors
                ):
                    interruption = True
            else:
                mutex_released = True

    if failure is not None and not commit_attempted:
        print(
            "Rollback attempted: "
            f"{'yes' if rollback_result is not None else 'no insertion session existed'}; "
            f"rollback call succeeded: "
            f"{rollback_result['call_succeeded'] if rollback_result is not None else 'N/A'}; "
            f"insertion session inactive: "
            f"{rollback_result['session_inactive'] if rollback_result is not None else 'N/A'}; "
            f"transaction object inactive: "
            f"{rollback_result['transaction_inactive'] if rollback_result is not None else 'N/A'}; "
            f"rollback state uncertain: "
            f"{rollback_result['state_uncertain'] if rollback_result is not None else 'N/A'}.",
            file=(
                sys.stderr
                if rollback_result is None or rollback_result["state_uncertain"]
                else sys.stdout
            ),
        )
        print(
            f"Fresh read-only synthetic-row absence result: {row_absence_status}.",
            file=sys.stderr if row_absence_status != "VERIFIED_ABSENT" else sys.stdout,
        )
        if row_absence_counts is not None:
            print(
                f"Observed counts for the exact synthetic identifiers: {row_absence_counts}.",
                file=sys.stderr if row_absence_status != "VERIFIED_ABSENT" else sys.stdout,
            )

    if failure is not None:
        print_sanitized_diagnostic(
            f"Failure during {failure_stage}",
            failure,
        )
    if rollback_result is not None and rollback_result["error"] is not None:
        print_sanitized_diagnostic(
            "Rollback call or transaction-state inspection failed",
            rollback_result["error"],
        )
    for label, exc in cleanup_errors:
        print_sanitized_diagnostic(label, exc)

    if commit_attempted and not commit_confirmed:
        print(
            "COMMIT_OUTCOME_UNCERTAIN: commit was attempted but did not return "
            "successfully. Records may already be committed. Do not rerun or "
            "delete records; inspect using a separately configured and verified "
            "read-only connection.",
            file=sys.stderr,
        )
        return 130 if interruption else 1

    if commit_confirmed and not post_commit_verification_passed:
        print(
            "Commit returned successfully, but post-commit verification failed. "
            "Records may already be committed. Do not rerun; inspect using a "
            "separately configured and verified read-only connection.",
            file=sys.stderr,
        )
        return 130 if interruption else 1

    if commit_confirmed and cleanup_errors:
        print(
            "Commit returned successfully, but one or more cleanup steps failed. "
            "Records may already be committed. Do not rerun; inspect using a "
            "separately configured and verified read-only connection.",
            file=sys.stderr,
        )
        return 130 if interruption else 1

    if failure is not None:
        print(
            "Pre-commit operation failed. Rollback status and row-absence "
            "inspection are reported separately; UNKNOWN does not establish "
            "that rows are absent. Do not automatically retry.",
            file=sys.stderr,
        )
        return 130 if interruption else 1

    mandatory_success = (
        preconditions_passed
        and precommit_eligibility_passed
        and commit_attempted
        and commit_confirmed
        and post_commit_verification_passed
        and insertion_session_closed
        and post_commit_session_closed
        and engine_disposed
        and application_engine_disposed
        and final_sidecar_check_passed
        and mutex_released
        and not cleanup_errors
    )
    if not mandatory_success:
        print(
            "A mandatory success condition was not confirmed. No success is "
            "claimed; inspect the isolated database read-only before any retry.",
            file=sys.stderr,
        )
        return 1

    print(
        "Synthetic recovery records committed; pre-commit and post-commit "
        "eligibility assertions passed; sessions closed; engines disposed; "
        "final sidecar check passed. Synthetic verification flags do not prove "
        "official college-source authenticity."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
