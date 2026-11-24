from backend.app.models import AcademicDataImportBatch, AcademicMomentum, RiskPrediction, RiskSnapshot, Student


def auth(token: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {token}'}


def test_student_crud_keeps_manual_origin_immutable_and_records_attendance(client, teacher_token, student_token):
    headers = auth(teacher_token)
    assert client.post('/api/v1/students', json={}, headers=auth(student_token)).status_code == 403
    created = client.post(
        '/api/v1/students',
        json={
            'student_id': 'MAN-001',
            'full_name': '  Alex Example ',
            'email': ' ALEX@EXAMPLE.EDU ',
            'semester': 'Fall 2026',
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    student = created.json()['student']
    assert student['full_name'] == 'Alex Example'
    assert student['email'] == 'alex@example.edu'
    assert student['semester'] == 'Fall 2026'
    assert student['data_origin'] == 'MANUALLY ADDED'

    attempted_reclassification = client.put(
        f"/api/v1/students/{student['id']}",
        json={'data_origin': 'INSTITUTIONAL IMPORT', 'attendance': 76},
        headers=headers,
    )
    assert attempted_reclassification.status_code == 422

    updated = client.put(
        f"/api/v1/students/{student['id']}",
        json={'attendance': 76, 'semester': 'Spring 2027'},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()['student']['data_origin'] == 'MANUALLY ADDED'
    assert updated.json()['student']['attendance'] == 76

    with client.app.state.test_session_factory() as db:
        record = db.query(Student).filter(Student.id == student['id']).one()
        observation = db.query(AcademicMomentum).filter(
            AcademicMomentum.student_id == record.id,
            AcademicMomentum.metric == 'attendance',
        ).one()
        batch = db.query(AcademicDataImportBatch).filter(
            AcademicDataImportBatch.id == observation.import_batch_id,
        ).one()
        assert batch.dataset_type == 'student_record_update'
        assert observation.value == 76

    duplicate = client.post(
        '/api/v1/students',
        json={'student_id': 'man-001', 'full_name': 'Different Person'},
        headers=headers,
    )
    assert duplicate.status_code == 409

    deleted = client.delete(f"/api/v1/students/{student['id']}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json() == {'deleted': True}


def test_student_csv_import_requires_institutional_attestation_and_is_idempotent(client, teacher_token):
    headers = auth(teacher_token)
    csv_content = (
        b'student_id,full_name,email,department,semester,attendance\n'
        b'IMP-001,Sam Import,sam@example.edu,Science,Fall 2026,82\n'
        b'IMP-002,Duplicate Email,sam@example.edu,Science,Fall 2026,75\n'
        b',Invalid Student,,Science,Fall 2026,80\n'
    )
    unverified = client.post(
        '/api/v1/students/import',
        headers=headers,
        data={
            'data_origin': 'INSTITUTIONAL IMPORT',
            'source_description': 'Registrar export',
            'source_verified': 'false',
        },
        files={'file': ('students.csv', csv_content, 'text/csv')},
    )
    assert unverified.status_code == 422

    imported = client.post(
        '/api/v1/students/import',
        headers=headers,
        data={
            'data_origin': 'INSTITUTIONAL IMPORT',
            'source_description': 'Registrar export, 2026 fall',
            'source_verified': 'true',
        },
        files={'file': ('students.csv', csv_content, 'text/csv')},
    )
    assert imported.status_code == 200, imported.text
    result = imported.json()
    assert (result['imported'], result['duplicates'], result['rejected']) == (1, 1, 1)
    assert result['source_provenance']['origin'] == 'INSTITUTIONAL IMPORT'
    assert result['source_provenance']['source_verified'] is True

    retry = client.post(
        '/api/v1/students/import',
        headers=headers,
        data={
            'data_origin': 'DEMO DATA',
            'source_description': '',
            'source_verified': 'false',
        },
        files={'file': ('students.csv', csv_content, 'text/csv')},
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()['duplicate_import'] is True
    assert retry.json()['source_provenance'] == result['source_provenance']

    with client.app.state.test_session_factory() as db:
        student = db.query(Student).filter(Student.student_id == 'IMP-001').one()
        assert student.data_origin == 'INSTITUTIONAL IMPORT'
        assert db.query(Student).filter(Student.student_id == 'IMP-002').count() == 0


def test_demo_model_never_scores_non_demo_students(client, teacher_token, monkeypatch):
    student_id = client.app.state.test_student_id
    with client.app.state.test_session_factory() as db:
        demo_student = db.query(Student).filter(Student.id == student_id).one()
        demo_student.attendance = 82
        demo_student.assignments_completed = 4
        institutional_student = Student(
            student_id='INST-001',
            full_name='Institutional Student',
            attendance=91,
            assignments_completed=5,
            data_origin='INSTITUTIONAL IMPORT',
        )
        db.add(institutional_student)
        db.commit()
        institutional_id = institutional_student.id

    calls: list[list[dict[str, float]]] = []

    def fake_predict_many(features, _artifact_path):
        calls.append(features)
        return [{
            'risk_level': 'LOW',
            'risk_probability': 0.8,
            'risk_probability_semantics': 'P(risk_level)',
            'class_probabilities': {'HIGH': 0.1, 'MEDIUM': 0.1, 'LOW': 0.8},
            'model_version': 'demo-test',
        } for _ in features]

    monkeypatch.setattr('backend.app.main.early_warning_uses_demo_model', lambda _db: True)
    monkeypatch.setattr('backend.app.main.demo_preview_artifact_path', lambda: 'demo-preview')
    monkeypatch.setattr('backend.app.main.predict_many_risks', fake_predict_many)

    response = client.get('/api/v1/students?limit=100', headers=auth(teacher_token))
    assert response.status_code == 200, response.text
    students = {item['id']: item for item in response.json()['items']}
    assert students[student_id]['risk_status'] == 'unvalidated-demo-model'
    assert students[institutional_id]['risk_status'] == 'unavailable'
    assert students[institutional_id]['risk_level'] == 'UNAVAILABLE'
    assert len(calls) == 1
    assert len(calls[0]) == 2


def test_demo_student_prediction_is_labeled_and_not_saved_as_validated_history(client, teacher_token, monkeypatch):
    student_id = client.app.state.test_student_id
    prediction = {
        'risk_level': 'LOW',
        'risk_probability': 0.8,
        'risk_probability_semantics': 'P(risk_level)',
        'risk_level_policy': 'argmax(class_probabilities)',
        'class_probabilities': {'HIGH': 0.1, 'MEDIUM': 0.1, 'LOW': 0.8},
        'model_version': 'demo-preview-test',
    }
    monkeypatch.setattr('backend.app.main.early_warning_uses_demo_model', lambda _db: True)
    monkeypatch.setattr('backend.app.main.predict_with_demo_preview', lambda _features: prediction)
    with client.app.state.test_session_factory() as db:
        before_predictions = db.query(RiskPrediction).count()
        before_snapshots = db.query(RiskSnapshot).count()

    response = client.post(
        '/api/v1/risk/predict',
        headers=auth(teacher_token),
        json={
            'student_id': student_id,
            'attendance': 80,
            'assignments_completed': 4,
            'activity_per_week': 0,
            'missing_submissions': 0,
            'attendance_change': 0,
            'assignment_submission_rate': 0,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()['risk_status'] == 'unvalidated-demo-model'
    assert 'not saved' in response.json()['risk_status_message']
    with client.app.state.test_session_factory() as db:
        assert db.query(RiskPrediction).count() == before_predictions
        assert db.query(RiskSnapshot).count() == before_snapshots
