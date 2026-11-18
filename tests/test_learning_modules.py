from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from backend.app.models import ApprovedTopic, Subject, SubjectModule, SubjectModuleResource, TopicLearningAssignment, Student, Teacher, User
from backend.app.services.auth import hash_password


def _syllabus_map():
    return {
        'course_title': 'Biology 200',
        'course_title_evidence': 'Biology 200',
        'module_number': 3,
        'module_title': 'Cell systems',
        'topics': [{
            'source_order': 1,
            'original_number': '3.1',
            'title': 'Membranes',
            'summary': 'Membranes regulate transport across cells in this syllabus section.',
            'learning_objectives': ['Describe membrane transport.'],
            'concepts': ['membranes'],
            'source_excerpt': '3.1 Membranes regulate transport across cells.',
            'source_locator': None,
            'order_uncertain': False,
            'hierarchy_uncertain': True,
            'requires_review': True,
            'review_reason': 'The indentation does not establish whether this is a parent topic.',
            'children': [{
                'source_order': 2,
                'original_number': '3.1.1',
                'title': 'Diffusion',
                'summary': 'Diffusion moves particles across a membrane in this syllabus section.',
                'learning_objectives': ['Describe diffusion.'],
                'concepts': ['diffusion'],
                'source_excerpt': '3.1.1 Diffusion moves particles across a membrane.',
                'source_locator': None,
                'order_uncertain': False,
                'hierarchy_uncertain': False,
                'requires_review': False,
                'review_reason': None,
                'children': [],
            }],
        }],
        'limitation': None,
    }


def _ensure_subject(client):
    with client.app.state.test_session_factory() as db:
        teacher_user = db.query(User).filter_by(email='teacher@example.com').one()
        teacher = Teacher(user_id=teacher_user.id, department='Science')
        subject = Subject(name='Biology', code='BIO-200', department='Science')
        db.add_all([teacher, subject])
        db.flush()
        course = db.query(Student).filter_by(id=client.app.state.test_student_id).one().course
        if course is None:
            from backend.app.models import Course
            course = Course(name='Biology 200', code='BIO-200-A', section='A', subject_id=subject.id, teacher_id=teacher.id)
            db.add(course)
            db.flush()
        student = db.query(Student).filter_by(id=client.app.state.test_student_id).one()
        student.course_id = course.id
        db.commit()
        return subject.id


def test_teacher_can_upload_module_resource_and_approve_topic(client, teacher_token, student_token, tmp_path, monkeypatch):
    subject_id = _ensure_subject(client)
    create_module = client.post(
        f'/api/v1/subjects/{subject_id}/modules',
        json={'module_number': 1, 'title': 'Cell biology', 'description': 'Core concepts'},
        headers={'Authorization': f'Bearer {teacher_token}'},
    )
    assert create_module.status_code == 201, create_module.text
    module_id = create_module.json()['id']

    resource = client.post(
        f'/api/v1/subjects/{subject_id}/modules/{module_id}/resources',
        files={'file': ('module.txt', b'Cell membranes regulate transport. Photosynthesis uses light energy. DNA stores genetic information.', 'text/plain')},
        headers={'Authorization': f'Bearer {teacher_token}'},
    )
    assert resource.status_code == 200, resource.text
    body = resource.json()
    assert body['processing_status'] == 'PROCESSED'
    assert body['file_type'] == 'TXT'
    assert 'Cell membranes' in (body['extracted_topics'][0]['title'] if body['extracted_topics'] else '') or body['error_message'] is None
    stored_path = Path(body['provenance']['storage_path'])
    assert stored_path.parent == tmp_path / 'uploads' / 'subject-modules' / str(subject_id) / str(module_id)
    assert stored_path.exists()
    monkeypatch.setattr('backend.app.main._extract_document_text', lambda *_args: (_ for _ in ()).throw(AssertionError('duplicate content should reuse persisted extraction')))
    duplicate_resource = client.post(
        f'/api/v1/subjects/{subject_id}/modules/{module_id}/resources',
        files={'file': ('module-copy.txt', b'Cell membranes regulate transport. Photosynthesis uses light energy. DNA stores genetic information.', 'text/plain')},
        headers={'Authorization': f'Bearer {teacher_token}'},
    )
    assert duplicate_resource.status_code == 200, duplicate_resource.text
    assert duplicate_resource.json()['processing_status'] == 'PROCESSED'
    assert duplicate_resource.json()['content_hash'] == body['content_hash']

    approval = client.post(
        f'/api/v1/subjects/{subject_id}/topics/approve',
        json={
            'resource_id': body['id'],
            'title': 'Cell membranes and DNA',
            'summary': 'This topic covers cell membranes, transport, and DNA storage of genetic information.',
            'learning_objectives': ['Describe transport across membranes', 'Explain DNA as genetic information'],
            'concepts': ['cell membrane', 'DNA', 'transport'],
            'approval_status': 'APPROVED',
        },
        headers={'Authorization': f'Bearer {teacher_token}'},
    )
    assert approval.status_code == 200, approval.text
    assert approval.json()['approval_status'] == 'APPROVED'

    modules = client.get(f'/api/v1/subjects/{subject_id}/modules', headers={'Authorization': f'Bearer {teacher_token}'})
    assert modules.status_code == 200
    assert modules.json()[0]['resource_count'] == 2
    resources = client.get(f'/api/v1/subjects/{subject_id}/modules/{module_id}/resources', headers={'Authorization': f'Bearer {teacher_token}'})
    assert resources.status_code == 200
    assert resources.json()[0]['content_hash'] == body['content_hash']
    assert resources.json()[0]['provenance']['source'] == 'teacher_upload'
    topics = client.get(f'/api/v1/subjects/{subject_id}/topics', headers={'Authorization': f'Bearer {teacher_token}'})
    assert topics.status_code == 200
    assert topics.json()[0]['approval_status'] == 'APPROVED'

    file_path = f'/api/v1/subjects/{subject_id}/modules/{module_id}/resources/{body["id"]}/file'
    viewed_file = client.get(file_path, headers={'Authorization': f'Bearer {teacher_token}'})
    assert viewed_file.status_code == 200
    assert b'Cell membranes regulate transport.' in viewed_file.content
    denied_file = client.get(file_path, headers={'Authorization': f'Bearer {student_token}'})
    assert denied_file.status_code == 403

    outside_path = tmp_path / 'outside-module.txt'
    outside_path.write_text('not authorized as a module file', encoding='utf-8')
    with client.app.state.test_session_factory() as db:
        persisted_resource = db.query(SubjectModuleResource).filter_by(id=body['id']).one()
        persisted_resource.storage_path = str(outside_path)
        db.commit()
    unsafe_file = client.get(file_path, headers={'Authorization': f'Bearer {teacher_token}'})
    assert unsafe_file.status_code == 404


def test_topic_extraction_and_approval_preserve_candidate_metadata_and_are_idempotent(client, teacher_token, student_token, monkeypatch):
    from backend.app import main as main_module

    subject_id = _ensure_subject(client)
    module = client.post(
        f'/api/v1/subjects/{subject_id}/modules',
        json={'module_number': 3, 'title': 'Cell systems'},
        headers={'Authorization': f'Bearer {teacher_token}'},
    ).json()
    upload = client.post(
        f"/api/v1/subjects/{subject_id}/modules/{module['id']}/resources",
        files={'file': ('biology-200.txt', b'Biology 200\n3.1 Membranes regulate transport across cells.\n3.1.1 Diffusion moves particles across a membrane.', 'text/plain')},
        headers={'Authorization': f'Bearer {teacher_token}'},
    )
    assert upload.status_code == 200, upload.text
    resource = upload.json()

    class TopicProvider:
        provider_name = 'mocked-ollama'

        def __init__(self):
            self.context = None

        def generate_topic_map(self, context):
            self.context = context
            return json.dumps(_syllabus_map())

    provider = TopicProvider()
    monkeypatch.setattr(main_module, 'build_provider', lambda _: provider)
    extraction_url = (
        f"/api/v1/subjects/{subject_id}/modules/{module['id']}"
        f"/resources/{resource['id']}/extract-topics"
    )
    extracted = client.post(extraction_url, headers={'Authorization': f'Bearer {teacher_token}'})

    assert extracted.status_code == 200, extracted.text
    body = extracted.json()
    assert body['topic_map_status'] == 'REVIEW_REQUIRED'
    assert provider.context['module'] == {'id': module['id'], 'number': 3, 'title': 'Cell systems'}
    assert body['extracted_topics'][0]['course_title'] == 'Biology 200'
    assert body['extracted_topics'][0]['module_number'] == 3
    assert body['extracted_topics'][0]['children'][0]['original_number'] == '3.1.1'
    assert body['extracted_topics'][0]['children'][0]['source_order'] == 2
    assert body['provenance']['topic_map_metadata']['source_filename'] == 'biology-200.txt'

    approval_payload = {
        'resource_id': resource['id'],
        'candidate_id': '1',
        'title': 'Membrane transport',
        'summary': 'Teacher correction: membranes regulate transport across cells.',
        'learning_objectives': ['Describe membrane transport.'],
        'concepts': ['membranes'],
        'approval_status': 'APPROVED',
    }
    approval_url = f'/api/v1/subjects/{subject_id}/topics/approve'
    approved = client.post(approval_url, json=approval_payload, headers={'Authorization': f'Bearer {teacher_token}'})
    assert approved.status_code == 200, approved.text
    provenance = approved.json()['source_provenance']
    assert provenance['candidate_id'] == '1'
    assert provenance['original_number'] == '3.1'
    assert provenance['source_order'] == 1
    assert provenance['source_filename'] == 'biology-200.txt'
    assert provenance['hierarchy_uncertain'] is True
    assert provenance['requires_review'] is True
    assert 'indentation' in provenance['review_reason']

    child_approval = client.post(
        approval_url,
        json={
            'resource_id': resource['id'],
            'candidate_id': '1.1',
            'title': 'Diffusion',
            'summary': 'Diffusion moves particles across a membrane as described in the source.',
            'learning_objectives': ['Describe diffusion.'],
            'concepts': ['diffusion'],
            'approval_status': 'APPROVED',
        },
        headers={'Authorization': f'Bearer {teacher_token}'},
    )
    assert child_approval.status_code == 200, child_approval.text
    child_provenance = child_approval.json()['source_provenance']
    assert child_provenance['course_title'] == 'Biology 200'
    assert child_provenance['module_number'] == 3
    assert child_provenance['original_number'] == '3.1.1'
    assert child_provenance['source_order'] == 2
    assert child_provenance['parent_candidate_id'] == '1'

    draft_approval = client.post(
        approval_url,
        json={
            'resource_id': resource['id'],
            'title': 'Unapproved candidate',
            'summary': 'This candidate has not been approved for use in student recovery.',
            'approval_status': 'DRAFT',
        },
        headers={'Authorization': f'Bearer {teacher_token}'},
    )
    assert draft_approval.status_code == 200, draft_approval.text
    assignment = client.post(
        f"/api/v1/students/{client.app.state.test_student_id}/topic-learning-assignments",
        json={'approved_topic_id': draft_approval.json()['id']},
        headers={'Authorization': f'Bearer {teacher_token}'},
    )
    assert assignment.status_code == 409

    repeated = client.post(approval_url, json=approval_payload, headers={'Authorization': f'Bearer {teacher_token}'})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()['id'] == approved.json()['id']
    assert repeated.json()['duplicate'] is True
    with client.app.state.test_session_factory() as db:
        persisted_topics = db.query(ApprovedTopic).filter_by(source_resource_id=resource['id']).all()
        assert len(persisted_topics) == 3
        assert sum(topic.source_provenance.get('candidate_id') == '1' for topic in persisted_topics) == 1

    other_module = client.post(
        f'/api/v1/subjects/{subject_id}/modules',
        json={'module_number': 4, 'title': 'Transport systems'},
        headers={'Authorization': f'Bearer {teacher_token}'},
    ).json()
    other_resource = client.post(
        f"/api/v1/subjects/{subject_id}/modules/{other_module['id']}/resources",
        files={'file': ('other-context.txt', b'Membranes regulate transport across cells.', 'text/plain')},
        headers={'Authorization': f'Bearer {teacher_token}'},
    ).json()
    same_title_other_context = client.post(
        approval_url,
        json={
            'resource_id': other_resource['id'],
            'title': 'Membrane transport',
            'summary': 'This is the same title in a different module and source context.',
            'approval_status': 'APPROVED',
        },
        headers={'Authorization': f'Bearer {teacher_token}'},
    )
    assert same_title_other_context.status_code == 200, same_title_other_context.text
    assert same_title_other_context.json()['id'] != approved.json()['id']

    denied_student = client.post(extraction_url, headers={'Authorization': f'Bearer {student_token}'})
    assert denied_student.status_code == 403
    with client.app.state.test_session_factory() as db:
        outside_user = User(
            email='outside-teacher@example.com',
            full_name='Outside Teacher',
            role='TEACHER',
            password_hash=hash_password('outside-teacher-pass'),
        )
        db.add(outside_user)
        db.flush()
        db.add(Teacher(user_id=outside_user.id, department='Science'))
        db.commit()
    outside_login = client.post(
        '/api/v1/auth/login',
        json={'email': 'outside-teacher@example.com', 'password': 'outside-teacher-pass'},
    )
    assert outside_login.status_code == 200
    denied_teacher = client.post(
        extraction_url,
        headers={'Authorization': f"Bearer {outside_login.json()['access_token']}"},
    )
    assert denied_teacher.status_code == 403


def test_pdf_page_locator_survives_candidate_approval(client, teacher_token, monkeypatch):
    from backend.app import main as main_module

    source_text = '[[[SOURCE_PAGE:2]]]\n3.1 Cell membranes regulate transport across cells.'
    monkeypatch.setattr(
        main_module,
        '_extract_document_text',
        lambda *_args: (source_text, [], None),
    )
    subject_id = _ensure_subject(client)
    module = client.post(
        f'/api/v1/subjects/{subject_id}/modules',
        json={'module_number': 3, 'title': 'Cell systems'},
        headers={'Authorization': f'Bearer {teacher_token}'},
    ).json()
    resource = client.post(
        f"/api/v1/subjects/{subject_id}/modules/{module['id']}/resources",
        files={'file': ('biology.pdf', b'pdf test fixture', 'application/pdf')},
        headers={'Authorization': f'Bearer {teacher_token}'},
    ).json()

    class PageProvider:
        provider_name = 'mocked-ollama'

        def generate_topic_map(self, _context):
            return json.dumps({
                'course_title': None,
                'course_title_evidence': None,
                'module_number': 3,
                'module_title': 'Cell systems',
                'topics': [{
                    'source_order': 1,
                    'original_number': '3.1',
                    'title': 'Cell membranes',
                    'summary': 'Cell membranes regulate transport across cells.',
                    'learning_objectives': ['Describe membrane transport.'],
                    'concepts': ['membranes'],
                    'source_excerpt': '3.1 Cell membranes regulate transport across cells.',
                    'source_locator': {'type': 'page', 'value': 2},
                    'order_uncertain': False,
                    'hierarchy_uncertain': False,
                    'requires_review': False,
                    'review_reason': None,
                    'children': [],
                }],
                'limitation': None,
            })

    monkeypatch.setattr(main_module, 'build_provider', lambda _: PageProvider())
    extracted = client.post(
        f"/api/v1/subjects/{subject_id}/modules/{module['id']}/resources/{resource['id']}/extract-topics",
        headers={'Authorization': f'Bearer {teacher_token}'},
    )
    assert extracted.status_code == 200, extracted.text
    assert extracted.json()['extracted_topics'][0]['source_locator'] == {'type': 'page', 'value': 2}

    approved = client.post(
        f'/api/v1/subjects/{subject_id}/topics/approve',
        json={
            'resource_id': resource['id'],
            'candidate_id': '1',
            'title': 'Cell membranes',
            'summary': 'Teacher-reviewed: cell membranes regulate transport across cells.',
            'approval_status': 'APPROVED',
        },
        headers={'Authorization': f'Bearer {teacher_token}'},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()['source_provenance']['source_locator'] == {'type': 'page', 'value': 2}


@pytest.mark.parametrize(
    ('responses', 'expected_calls', 'expected_candidates'),
    [
        (['not-json', 'still-not-json'], 2, 0),
        (['not-json', json.dumps(_syllabus_map())], 2, 1),
        ([json.dumps(_syllabus_map()), 'still-not-json'], 2, 1),
    ],
)
def test_topic_extraction_retries_invalid_json_and_keeps_review_state(
    client, teacher_token, monkeypatch, responses, expected_calls, expected_candidates,
):
    from backend.app import main as main_module

    subject_id = _ensure_subject(client)
    module = client.post(
        f'/api/v1/subjects/{subject_id}/modules',
        json={'module_number': 3, 'title': 'Cell systems'},
        headers={'Authorization': f'Bearer {teacher_token}'},
    ).json()
    resource = client.post(
        f"/api/v1/subjects/{subject_id}/modules/{module['id']}/resources",
        files={'file': ('syllabus.txt', b'Biology 200\n3.1 Membranes regulate transport across cells.\n3.1.1 Diffusion moves particles across a membrane.', 'text/plain')},
        headers={'Authorization': f'Bearer {teacher_token}'},
    ).json()

    class RetryProvider:
        provider_name = 'mocked-ollama'
        calls = 0

        def generate_topic_map(self, context):
            result = responses[self.calls]
            self.calls += 1
            return result

    provider = RetryProvider()
    monkeypatch.setattr(main_module, 'build_provider', lambda _: provider)
    response = client.post(
        f"/api/v1/subjects/{subject_id}/modules/{module['id']}/resources/{resource['id']}/extract-topics",
        headers={'Authorization': f'Bearer {teacher_token}'},
    )

    assert response.status_code == 200, response.text
    assert provider.calls == expected_calls
    assert response.json()['topic_map_status'] == 'REVIEW_REQUIRED'
    assert len(response.json()['extracted_topics']) == expected_candidates
    if not expected_candidates:
        assert 'valid JSON' in response.json()['error_message']


@pytest.mark.parametrize('provider_error', ['Ollama service is unavailable.', 'Ollama topic extraction timed out.'])
def test_topic_extraction_provider_failures_return_review_required(client, teacher_token, monkeypatch, provider_error):
    from backend.app import main as main_module

    subject_id = _ensure_subject(client)
    module = client.post(
        f'/api/v1/subjects/{subject_id}/modules',
        json={'module_number': 3, 'title': 'Cell systems'},
        headers={'Authorization': f'Bearer {teacher_token}'},
    ).json()
    resource = client.post(
        f"/api/v1/subjects/{subject_id}/modules/{module['id']}/resources",
        files={'file': ('syllabus.txt', b'Biology 200\n3.1 Membranes regulate transport across cells.', 'text/plain')},
        headers={'Authorization': f'Bearer {teacher_token}'},
    ).json()

    class UnavailableProvider:
        provider_name = 'ollama-local'

        def generate_topic_map(self, context):
            raise RuntimeError(provider_error)

    monkeypatch.setattr(main_module, 'build_provider', lambda _: UnavailableProvider())
    response = client.post(
        f"/api/v1/subjects/{subject_id}/modules/{module['id']}/resources/{resource['id']}/extract-topics",
        headers={'Authorization': f'Bearer {teacher_token}'},
    )

    assert response.status_code == 200, response.text
    assert response.json()['topic_map_status'] == 'REVIEW_REQUIRED'
    assert provider_error in response.json()['error_message']


def test_module_upload_enforces_teacher_subject_and_file_constraints(client, teacher_token, student_token, monkeypatch):
    subject_id = _ensure_subject(client)
    module = client.post(
        f'/api/v1/subjects/{subject_id}/modules',
        json={'module_number': 1, 'title': 'Cell biology'},
        headers={'Authorization': f'Bearer {teacher_token}'},
    ).json()

    assert client.get(f'/api/v1/subjects/{subject_id}/modules', headers={'Authorization': f'Bearer {student_token}'}).status_code == 403
    with client.app.state.test_session_factory() as db:
        from backend.app.models import Subject
        other_subject = Subject(name='Mathematics', code='MATH-100', department='Mathematics')
        db.add(other_subject)
        db.commit()
        other_subject_id = other_subject.id
    subject_list = client.get('/api/v1/subjects', headers={'Authorization': f'Bearer {teacher_token}'})
    assert subject_list.status_code == 200
    assert any(item['id'] == subject_id for item in subject_list.json())
    assert all(item['id'] != other_subject_id for item in subject_list.json())
    forbidden = client.post(
        f'/api/v1/subjects/{other_subject_id}/modules',
        json={'module_number': 1, 'title': 'Unauthorized module'},
        headers={'Authorization': f'Bearer {teacher_token}'},
    )
    assert forbidden.status_code == 403

    unsupported = client.post(
        f"/api/v1/subjects/{subject_id}/modules/{module['id']}/resources",
        files={'file': ('script.exe', b'not a document', 'application/octet-stream')},
        headers={'Authorization': f'Bearer {teacher_token}'},
    )
    assert unsupported.status_code == 415

    empty_document = client.post(
        f"/api/v1/subjects/{subject_id}/modules/{module['id']}/resources",
        files={'file': ('empty.txt', b'', 'text/plain')},
        headers={'Authorization': f'Bearer {teacher_token}'},
    )
    assert empty_document.status_code == 200
    assert empty_document.json()['processing_status'] == 'FAILED'
    assert 'No readable text' in empty_document.json()['error_message']

    monkeypatch.setattr('backend.app.main.settings.max_upload_size', 4)
    oversized = client.post(
        f"/api/v1/subjects/{subject_id}/modules/{module['id']}/resources",
        files={'file': ('large.txt', b'12345', 'text/plain')},
        headers={'Authorization': f'Bearer {teacher_token}'},
    )
    assert oversized.status_code == 413


def test_document_text_extraction_emits_trustworthy_pdf_and_pptx_locator_markers(tmp_path, monkeypatch):
    import pypdf
    import sys
    from types import SimpleNamespace
    from backend.app import main as main_module

    monkeypatch.setattr(
        pypdf,
        'PdfReader',
        lambda _path: SimpleNamespace(pages=[
            SimpleNamespace(extract_text=lambda: 'Page content with [[[SOURCE_PAGE:9]]] as literal text.'),
            SimpleNamespace(extract_text=lambda: 'Second page content.'),
        ]),
    )
    pdf_text, _, pdf_error = main_module._extract_document_text(tmp_path / 'source.pdf', '.pdf')
    assert pdf_error is None
    assert '[[[SOURCE_PAGE:1]]]' in pdf_text
    assert '[[[SOURCE_PAGE:2]]]' in pdf_text
    assert '[[[SOURCE PAGE:9]]]' in pdf_text

    fake_pptx = SimpleNamespace(Presentation=lambda _path: SimpleNamespace(slides=[
            SimpleNamespace(shapes=[SimpleNamespace(text='Slide one content.')]),
            SimpleNamespace(shapes=[SimpleNamespace(text='Slide two content.')]),
        ]))
    monkeypatch.setitem(sys.modules, 'pptx', fake_pptx)
    pptx_text, _, pptx_error = main_module._extract_document_text(tmp_path / 'source.pptx', '.pptx')
    assert pptx_error is None
    assert '[[[SOURCE_SLIDE:1]]]' in pptx_text
    assert '[[[SOURCE_SLIDE:2]]]' in pptx_text


def test_department_membership_alone_does_not_authorize_module_management(client, teacher_token):
    _ensure_subject(client)
    with client.app.state.test_session_factory() as db:
        same_department_subject = Subject(
            name='Chemistry',
            code='CHEM-SCI-200',
            department='Science',
        )
        db.add(same_department_subject)
        db.commit()
        subject_id = same_department_subject.id

    denied_list = client.get(
        f'/api/v1/subjects/{subject_id}/modules',
        headers={'Authorization': f'Bearer {teacher_token}'},
    )
    denied_create = client.post(
        f'/api/v1/subjects/{subject_id}/modules',
        json={'module_number': 3, 'title': 'Module 3'},
        headers={'Authorization': f'Bearer {teacher_token}'},
    )

    assert denied_list.status_code == 403
    assert denied_create.status_code == 403


def test_topic_assignments_follow_module_source_order_per_student(client, teacher_token):
    subject_id = _ensure_subject(client)
    student_id = client.app.state.test_student_id

    def add_topic(module_number, title, content):
        module = client.post(
            f'/api/v1/subjects/{subject_id}/modules',
            json={'module_number': module_number, 'title': title},
            headers={'Authorization': f'Bearer {teacher_token}'},
        ).json()
        resource = client.post(
            f'/api/v1/subjects/{subject_id}/modules/{module["id"]}/resources',
            files={'file': (f'{module_number}.txt', content.encode(), 'text/plain')},
            headers={'Authorization': f'Bearer {teacher_token}'},
        ).json()
        topic = client.post(
            f'/api/v1/subjects/{subject_id}/topics/approve',
            json={
                'resource_id': resource['id'], 'title': title,
                'summary': f'This teacher-approved summary explains {title} from the source.',
                'learning_objectives': [f'Describe {title}'], 'concepts': [title], 'approval_status': 'APPROVED',
            },
            headers={'Authorization': f'Bearer {teacher_token}'},
        )
        assert topic.status_code == 200, topic.text
        return topic.json()

    first_topic = add_topic(1, 'Cell foundations', 'Cells are the basic units of life.')
    second_topic = add_topic(2, 'Cell division', 'Cell division produces new cells.')
    second_url = f'/api/v1/students/{student_id}/topic-learning-assignments'
    skipped = client.post(second_url, json={'approved_topic_id': second_topic['id']}, headers={'Authorization': f'Bearer {teacher_token}'})
    assert skipped.status_code == 409
    assert 'Cell foundations' in skipped.json()['detail']

    first = client.post(second_url, json={'approved_topic_id': first_topic['id']}, headers={'Authorization': f'Bearer {teacher_token}'})
    assert first.status_code == 200, first.text
    repeated = client.post(second_url, json={'approved_topic_id': first_topic['id']}, headers={'Authorization': f'Bearer {teacher_token}'})
    assert repeated.status_code == 200
    assert repeated.json()['id'] == first.json()['id']
    with client.app.state.test_session_factory() as db:
        db.query(TopicLearningAssignment).filter_by(id=first.json()['id']).one().status = 'MASTERED'
        db.commit()
    second = client.post(second_url, json={'approved_topic_id': second_topic['id']}, headers={'Authorization': f'Bearer {teacher_token}'})
    assert second.status_code == 200, second.text


def test_student_can_receive_topic_assignment_and_attempt_submission(client, teacher_token, student_token, monkeypatch):
    subject_id = _ensure_subject(client)
    module = client.post(
        f'/api/v1/subjects/{subject_id}/modules',
        json={'module_number': 2, 'title': 'Genetics', 'description': 'Genes and inheritance'},
        headers={'Authorization': f'Bearer {teacher_token}'},
    ).json()
    resource = client.post(
        f'/api/v1/subjects/{subject_id}/modules/{module["id"]}/resources',
        files={'file': ('genetics.txt', b'Genes are inherited units. Mutation changes DNA sequences.', 'text/plain')},
        headers={'Authorization': f'Bearer {teacher_token}'},
    ).json()
    topic = client.post(
        f'/api/v1/subjects/{subject_id}/topics/approve',
        json={
            'resource_id': resource['id'],
            'title': 'Genes and mutation',
            'summary': 'This topic covers genes and mutation in DNA sequences.',
            'learning_objectives': ['Define a gene', 'Describe mutation'],
            'concepts': ['gene', 'mutation', 'DNA'],
            'approval_status': 'APPROVED',
        },
        headers={'Authorization': f'Bearer {teacher_token}'},
    ).json()

    assignment = client.post(
        f"/api/v1/students/{client.app.state.test_student_id}/topic-learning-assignments",
        json={
            'approved_topic_id': topic['id'],
            'learning_objective': 'Describe what a gene is and how mutation affects DNA.',
            'lesson_content': 'Genes are inherited units stored in DNA. Mutation changes the sequence and may alter function.',
            'expected_duration_minutes': 40,
            'scheduled_at': datetime.utcnow().isoformat(),
            'mastery_criteria': 'Score 80% and explain the key idea.',
        },
        headers={'Authorization': f'Bearer {teacher_token}'},
    )
    assert assignment.status_code == 200, assignment.text
    assignment_id = assignment.json()['id']

    with client.app.state.test_session_factory() as db:
        other_student_id = db.query(Student).join(User).filter(User.email == 'other@example.com').one().id
    unauthorized_assignment = client.post(
        f'/api/v1/students/{other_student_id}/topic-learning-assignments',
        json={'approved_topic_id': topic['id']},
        headers={'Authorization': f'Bearer {teacher_token}'},
    )
    assert unauthorized_assignment.status_code == 404

    with client.app.state.test_session_factory() as db:
        from backend.app.models import ApprovedTopic, Course, Subject, SubjectModuleResource

        teacher = db.query(Teacher).join(User).filter(User.email == 'teacher@example.com').one()
        student = db.query(Student).filter_by(id=client.app.state.test_student_id).one()
        other_subject = Subject(name='Mathematics', code='MATH-200', department='Science')
        db.add(other_subject)
        db.flush()
        other_course = Course(
            name='Mathematics 200',
            code='MATH-200-A',
            section='A',
            subject_id=other_subject.id,
            teacher_id=teacher.id,
        )
        db.add(other_course)
        db.flush()
        other_module = SubjectModule(subject_id=other_subject.id, module_number=1, title='Algebra')
        db.add(other_module)
        db.flush()
        other_resource = SubjectModuleResource(
            module_id=other_module.id,
            subject_id=other_subject.id,
            teacher_id=teacher.id,
            title='Algebra source',
            original_filename='algebra.txt',
            storage_path='not-read-by-this-test',
            file_type='TXT',
            file_size=20,
            content_hash='c' * 64,
            processing_status='PROCESSED',
            extracted_text='A linear equation contains a variable.',
        )
        db.add(other_resource)
        db.flush()
        other_topic = ApprovedTopic(
            subject_id=other_subject.id,
            module_id=other_module.id,
            source_resource_id=other_resource.id,
            teacher_id=teacher.id,
            title='Linear equations',
            summary='Variables in linear equations.',
            learning_objectives=['Identify a variable.'],
            concepts=['variable'],
            approval_status='APPROVED',
            approved_by_user_id=teacher.user_id,
            source_provenance={'resource_id': other_resource.id},
        )
        db.add(other_topic)
        db.flush()
        other_assignment = TopicLearningAssignment(
            student_id=student.id,
            subject_id=other_subject.id,
            module_id=other_module.id,
            approved_topic_id=other_topic.id,
            teacher_id=teacher.id,
            title=other_topic.title,
            learning_objective='Identify a variable.',
            lesson_content='A variable represents a value.',
            expected_duration_minutes=30,
            status='ACTIVE',
            source_provenance={'source': 'teacher_test_fixture'},
        )
        db.add(other_assignment)
        db.commit()
        other_assignment_id = other_assignment.id
        other_subject_id = other_subject.id
        other_module_id = other_module.id
        other_topic_id = other_topic.id

    unrelated_resource = client.post(
        f'/api/v1/subjects/{subject_id}/modules/{module["id"]}/resources',
        files={'file': ('teacher-notes.txt', b'Unrelated private teacher notes.', 'text/plain')},
        headers={'Authorization': f'Bearer {teacher_token}'},
    ).json()
    allowed_download = client.get(
        f'/api/v1/topic-learning-assignments/{assignment_id}/resources/{resource["id"]}',
        headers={'Authorization': f'Bearer {student_token}'},
    )
    assert allowed_download.status_code == 200
    assert allowed_download.content == b'Genes are inherited units. Mutation changes DNA sequences.'
    assert client.get(
        f'/api/v1/topic-learning-assignments/{assignment_id}/resources/{unrelated_resource["id"]}',
        headers={'Authorization': f'Bearer {student_token}'},
    ).status_code == 404

    class TestProvider:
        def generate_topic_assessment(self, context):
            passage = context['passages'][0]
            quote = passage['text'][:40]
            questions = []
            mcq_prompts = [
                'What fact identifies inheritance in this gene passage?',
                'Which description best defines the gene concept in the source?',
                'How does DNA relate to inherited units in this reading?',
                'What is stated about units passed across generations?',
                'Which concept is described as inherited in the source?',
                'How does the source characterize a gene?',
                'What evidence supports this definition of biological units?',
                'Which term concerns an inherited unit in the reading?',
                'What biological information does the passage assign to genes?',
                'How are genes described relative to inheritance?',
                'Which phrase connects genes and inherited units?',
                'What does the source passage say genes represent?',
                'Which statement matches the source description of genes?',
                'How are genes classified by this source?',
                'What idea about inheritance appears in the source?',
            ]
            for index in range(15):
                questions.append({
                    'question_type': 'MCQ', 'question': mcq_prompts[index],
                    'options': ['Unsupported claim', 'Genes are inherited units', 'Unrelated detail', 'No evidence'],
                    'correct_answer': 'B', 'explanation': 'The provided source states this.',
                    'learning_objective': 'Describe genes', 'difficulty': 'FOUNDATION',
                    'source_evidence': [{'ref': passage['ref'], 'quote': quote}],
                })
            short_prompts = [
                'State what the passage says genes are.',
                'How are genes related to inheritance?',
                'What unit is described as inherited?',
                'Explain the source definition of genes.',
                'What does the reading identify about genes?',
            ]
            for index in range(5):
                questions.append({
                    'question_type': 'SHORT_ANSWER', 'question': short_prompts[index],
                    'options': [], 'correct_answer': 'Genes are inherited units.',
                    'explanation': 'Name the source concept.', 'learning_objective': 'Describe genes',
                    'difficulty': 'FOUNDATION',
                    'rubric': {'criteria': [{'criterion': 'Names genes', 'marks': 5, 'partial_credit': 'Award partial credit for mentioning inheritance.'}], 'max_marks': 5},
                    'source_evidence': [{'ref': passage['ref'], 'quote': quote}],
                })
            return json.dumps({'questions': questions})

        def evaluate_short_answer(self, context):
            return json.dumps({'marks_awarded': 0.5, 'feedback': 'The source concept is partially present.', 'confidence': 0.45, 'needs_review': False, 'criteria': []})

    monkeypatch.setattr('backend.app.main.retrieve_scoped', lambda query, documents, top_k=8: [{
        'ref': f"{documents[0]['ref']}#chunk-1", 'source': documents[0]['source'],
        'checksum': documents[0]['checksum'], 'text': documents[0]['text'], 'score': 0.9,
    }])
    monkeypatch.setattr('backend.app.main.build_provider', lambda settings: TestProvider())
    assessment = client.post(
        f'/api/v1/topic-learning-assignments/{assignment_id}/generate-assessment',
        headers={'Authorization': f'Bearer {teacher_token}'},
    )
    assert assessment.status_code == 200, assessment.text
    assessment_body = assessment.json()
    assert assessment_body['question_count'] == 20
    assert assessment_body['total_marks'] == 100
    assert sum(question['question_type'] == 'MCQ' for question in assessment_body['questions']) == 15
    assert sum(question['question_type'] == 'SHORT_ANSWER' for question in assessment_body['questions']) == 5
    assert assessment_body['status'] == 'REVIEW_REQUIRED'
    assert assessment_body['validation_results']['valid'] is True
    assert {question['marks'] for question in assessment_body['questions']} == {5}
    assessment_id = assessment_body['id']

    with client.app.state.test_session_factory() as db:
        from backend.app.models import AssessmentQuestion

        first_question = db.query(AssessmentQuestion).filter_by(assessment_id=assessment_id).order_by(AssessmentQuestion.id).first()
        assert first_question is not None
        first_question.question_type = 'SHORT_ANSWER'
        first_question.marks = 4
        db.commit()
    invalid_approval = client.post(
        f'/api/v1/learning-assessments/{assessment_id}/approve',
        json={},
        headers={'Authorization': f'Bearer {teacher_token}'},
    )
    assert invalid_approval.status_code == 422
    assert any('exactly 15 MCQs and 5 short-answer' in issue for issue in invalid_approval.json()['detail']['issues'])
    assert any('exactly five marks' in issue for issue in invalid_approval.json()['detail']['issues'])
    assert any('exactly 100 marks' in issue for issue in invalid_approval.json()['detail']['issues'])
    with client.app.state.test_session_factory() as db:
        first_question = db.query(AssessmentQuestion).filter_by(assessment_id=assessment_id).order_by(AssessmentQuestion.id).first()
        assert first_question is not None
        first_question.question_type = 'MCQ'
        first_question.marks = 5
        db.commit()

    with client.app.state.test_session_factory() as db:
        first_question = db.query(AssessmentQuestion).filter_by(assessment_id=assessment_id).order_by(AssessmentQuestion.id).first()
        assert first_question is not None
        original_scope = {
            'subject_id': first_question.subject_id,
            'module_id': first_question.module_id,
            'topic_id': first_question.topic_id,
        }
    for field, other_id in (
        ('subject_id', other_subject_id),
        ('module_id', other_module_id),
        ('topic_id', other_topic_id),
    ):
        with client.app.state.test_session_factory() as db:
            first_question = db.query(AssessmentQuestion).filter_by(assessment_id=assessment_id).order_by(AssessmentQuestion.id).first()
            assert first_question is not None
            setattr(first_question, field, other_id)
            db.commit()
        mismatched_approval = client.post(
            f'/api/v1/learning-assessments/{assessment_id}/approve',
            json={},
            headers={'Authorization': f'Bearer {teacher_token}'},
        )
        assert mismatched_approval.status_code == 422, f'{field}: {mismatched_approval.text}'
        assert any(
            'Persisted questions must belong to the assigned subject, module, and approved topic.'
            in issue
            for issue in mismatched_approval.json()['detail']['issues']
        )
        with client.app.state.test_session_factory() as db:
            first_question = db.query(AssessmentQuestion).filter_by(assessment_id=assessment_id).order_by(AssessmentQuestion.id).first()
            assert first_question is not None
            setattr(first_question, field, original_scope[field])
            db.commit()
    assert client.post(f'/api/v1/learning-assessments/{assessment_id}/publish', headers={'Authorization': f'Bearer {teacher_token}'}).status_code == 409
    assert client.post(f'/api/v1/learning-assessments/{assessment_id}/approve', json={}, headers={'Authorization': f'Bearer {teacher_token}'}).status_code == 200
    published = client.post(f'/api/v1/learning-assessments/{assessment_id}/publish', headers={'Authorization': f'Bearer {teacher_token}'})
    assert published.status_code == 200, published.text

    student_assignments = client.get(
        '/api/v1/students/me/topic-learning-assignments',
        headers={'Authorization': f'Bearer {student_token}'},
    )
    assert student_assignments.status_code == 200, student_assignments.text
    student_assignment = next(item for item in student_assignments.json() if item['id'] == assignment_id)
    student_assessment = student_assignment['assessment']
    assert student_assessment['status'] == 'PUBLISHED'

    session_start = client.post(f'/api/v1/topic-learning-assignments/{assignment_id}/study-session', headers={'Authorization': f'Bearer {student_token}'})
    assert session_start.status_code == 200
    assert session_start.json()['ready'] is False
    assert client.post(f'/api/v1/topic-learning-assignments/{assignment_id}/attempts', headers={'Authorization': f'Bearer {student_token}'}).status_code == 409
    with client.app.state.test_session_factory() as db:
        from backend.app.models import LearningStudySession
        study_session = db.query(LearningStudySession).filter_by(assignment_id=assignment_id).one()
        study_session.started_at = datetime.utcnow() - timedelta(minutes=41)
        db.commit()
    assert client.post(f'/api/v1/topic-learning-assignments/{assignment_id}/study-session/complete', headers={'Authorization': f'Bearer {student_token}'}).status_code == 200

    attempt = client.post(
        f'/api/v1/topic-learning-assignments/{assignment_id}/attempts',
        headers={'Authorization': f'Bearer {student_token}'},
    )
    assert attempt.status_code == 200, attempt.text
    attempt_id = attempt.json()['id']
    assert all('correct_answer' not in question for question in attempt.json()['question_set']['questions'])

    answers = {}
    for question in assessment_body['questions']:
        qid = question['id']
        if question['question_type'] == 'MCQ':
            answers[str(qid)] = 'A' if qid == assessment_body['questions'][0]['id'] else 'B'
        else:
            answers[str(qid)] = 'gene and mutation affect DNA'

    submission = client.post(
        f'/api/v1/assessment-attempts/{attempt_id}/submit',
        json={'answers': answers},
        headers={'Authorization': f'Bearer {student_token}'},
    )
    assert submission.status_code == 200, submission.text
    payload = submission.json()
    assert payload['status'] == 'AWAITING_REVIEW'
    assert payload['review_pending'] is True
    first_response = next(
        response for response in payload['responses']
        if response['question_id'] == assessment_body['questions'][0]['id']
    )
    assert first_response['feedback'] == 'Incorrect. Review the approved source material and try again in a future attempt.'
    assert 'Genes are inherited units.' not in submission.text
    review = client.get(f'/api/v1/assessment-attempts/{attempt_id}/review', headers={'Authorization': f'Bearer {teacher_token}'})
    assert review.status_code == 200, review.text
    short_answers = [item for item in review.json()['questions'] if item['question_type'] == 'SHORT_ANSWER']
    assert any(
        question['question_type'] == 'MCQ' and question['correct_answer'] == 'B'
        for question in review.json()['questions']
    )
    assert len(short_answers) == 5
    for index, question in enumerate(short_answers):
        decision = 'override' if index == 0 else 'accept'
        response = client.put(
            f'/api/v1/assessment-attempts/{attempt_id}/answers/{question["id"]}/review',
            json={'decision': decision, **({'marks_awarded': 1.25, 'reason': 'Awarded partial credit under the source-based rubric.'} if decision == 'override' else {})},
            headers={'Authorization': f'Bearer {teacher_token}'},
        )
        assert response.status_code == 200, response.text
    final_review = client.get(f'/api/v1/assessment-attempts/{attempt_id}/review', headers={'Authorization': f'Bearer {teacher_token}'})
    assert final_review.json()['status'] == 'SUBMITTED'
    assert final_review.json()['percentage'] == 81.25
    assert client.post(f'/api/v1/topic-learning-assignments/{assignment_id}/attempts', headers={'Authorization': f'Bearer {student_token}'}).status_code == 409
    with client.app.state.test_session_factory() as db:
        from backend.app.models import AssessmentAttempt, AssessmentAttemptAnswer, AuditLog
        overridden = db.query(AssessmentAttemptAnswer).filter_by(attempt_id=attempt_id, marks_awarded=1.25).one()
        assert overridden.original_marks_awarded == 2.5
        assert overridden.marks_awarded == 1.25
        assert db.query(AuditLog).filter_by(action='learning_answer.overridden').count() == 1
        assert db.query(TopicLearningAssignment).filter_by(id=assignment_id).one().status == 'MASTERED'
        assert db.query(TopicLearningAssignment).filter_by(id=other_assignment_id).one().status == 'ACTIVE'
        assert db.query(AssessmentAttempt).filter_by(assignment_id=other_assignment_id).count() == 0
        assert db.query(AssessmentAttempt).filter_by(id=attempt_id).one().assignment_id == assignment_id
