from backend.app.models import ApprovedTopic, Course, Student, Subject, SubjectModule, Teacher, TopicLearningAssignment, User


def _eligible_focus(student_id: int, subject_ids: list[int]) -> dict:
    return {
        'evidence_scope': 'FIRST_INTERNAL',
        'policy': {'active_for_classification': True, 'threshold_maximum': 25},
        'subjects': [{
            'id': subject_id,
            'students': [{
                'id': student_id,
                'latest_assessment': {
                    'evidence_status': 'VERIFIED',
                    'maximum_marks_verified': True,
                    'normalized_score': 11.5,
                },
            }],
        } for subject_id in subject_ids],
    }


def test_module_four_assignment_waits_for_module_three_in_every_eligible_subject(
    client,
    teacher_token,
    monkeypatch,
):
    student_id = client.app.state.test_student_id
    with client.app.state.test_session_factory() as db:
        teacher_user = db.query(User).filter_by(email='teacher@example.com').one()
        teacher = Teacher(user_id=teacher_user.id, department='Science')
        biology = Subject(name='Biology', code='BIO-REC', department='Science')
        chemistry = Subject(name='Chemistry', code='CHEM-REC', department='Science')
        db.add_all([teacher, biology, chemistry])
        db.flush()
        biology_course = Course(name='Biology A', code='BIO-REC-A', section='A', subject_id=biology.id, teacher_id=teacher.id)
        chemistry_course = Course(name='Chemistry A', code='CHEM-REC-A', section='A', subject_id=chemistry.id, teacher_id=teacher.id)
        db.add_all([biology_course, chemistry_course])
        student = db.query(Student).filter_by(id=student_id).one()
        student.course_id = biology_course.id
        biology_module_three = SubjectModule(subject_id=biology.id, module_number=3, title='Biology Module 3')
        chemistry_module_three = SubjectModule(subject_id=chemistry.id, module_number=3, title='Chemistry Module 3')
        biology_module_four = SubjectModule(subject_id=biology.id, module_number=4, title='Biology Module 4')
        db.add_all([biology_module_three, chemistry_module_three, biology_module_four])
        db.flush()
        biology_topic = ApprovedTopic(
            subject_id=biology.id, module_id=biology_module_three.id, title='Biology M3 topic',
            summary='Biology module three source topic.', approval_status='APPROVED',
        )
        chemistry_topic = ApprovedTopic(
            subject_id=chemistry.id, module_id=chemistry_module_three.id, title='Chemistry M3 topic',
            summary='Chemistry module three source topic.', approval_status='APPROVED',
        )
        biology_module_four_topic = ApprovedTopic(
            subject_id=biology.id, module_id=biology_module_four.id, title='Biology M4 topic',
            summary='Biology module four source topic.', approval_status='APPROVED',
        )
        db.add_all([biology_topic, chemistry_topic, biology_module_four_topic])
        db.flush()
        db.add_all([
            TopicLearningAssignment(
                student_id=student_id, subject_id=biology.id, module_id=biology_module_three.id,
                approved_topic_id=biology_topic.id, title=biology_topic.title,
                learning_objective='Biology learning objective.', lesson_content='Biology lesson.',
                status='MASTERED',
            ),
            TopicLearningAssignment(
                student_id=student_id, subject_id=chemistry.id, module_id=chemistry_module_three.id,
                approved_topic_id=chemistry_topic.id, title=chemistry_topic.title,
                learning_objective='Chemistry learning objective.', lesson_content='Chemistry lesson.',
                status='ACTIVE',
            ),
        ])
        db.commit()
        biology_id = biology.id
        chemistry_id = chemistry.id
        module_four_topic_id = biology_module_four_topic.id

    monkeypatch.setattr(
        'backend.app.main.build_teacher_subject_focus',
        lambda _db, _user, **_kwargs: _eligible_focus(student_id, [biology_id, chemistry_id]),
    )
    headers = {'Authorization': f'Bearer {teacher_token}'}
    endpoint = f'/api/v1/students/{student_id}/topic-learning-assignments'
    payload = {'approved_topic_id': module_four_topic_id}

    blocked = client.post(endpoint, json=payload, headers=headers)
    assert blocked.status_code == 409
    assert 'every approved Module 3 topic' in blocked.json()['detail']

    with client.app.state.test_session_factory() as db:
        chemistry_assignment = db.query(TopicLearningAssignment).filter_by(
            student_id=student_id,
            subject_id=chemistry_id,
        ).one()
        chemistry_assignment.status = 'MASTERED'
        db.commit()

    allowed = client.post(endpoint, json=payload, headers=headers)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()['approved_topic_id'] == module_four_topic_id
    assert allowed.json()['subject_id'] == biology_id


def test_strict_recovery_eligibility_requires_verified_fa1_below_twelve():
    from backend.app.main import _strict_recovery_subject_ids

    focus = {
        'evidence_scope': 'FIRST_INTERNAL',
        'policy': {'active_for_classification': True, 'threshold_maximum': 25},
        'subjects': [
            {'id': 1, 'students': [{'id': 17, 'latest_assessment': {
                'evidence_status': 'VERIFIED', 'maximum_marks_verified': True, 'normalized_score': 11.99,
            }}]},
            {'id': 2, 'students': [{'id': 17, 'latest_assessment': {
                'evidence_status': 'VERIFIED', 'maximum_marks_verified': True, 'normalized_score': 12,
            }}]},
            {'id': 3, 'students': [{'id': 17, 'latest_assessment': {
                'evidence_status': 'INSUFFICIENT_EVIDENCE', 'maximum_marks_verified': False, 'normalized_score': 5,
            }}]},
        ],
    }

    assert _strict_recovery_subject_ids(focus, 17) == {1}
