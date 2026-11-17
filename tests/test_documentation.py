"""验证调用者能从 OpenAPI 与代码中查到接口、参数及函数用途。"""
import ast
from pathlib import Path

from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def test_every_operation_and_parameter_has_description():
    schema = create_app().openapi()
    operations = [operation for item in schema['paths'].values()
                  for method, operation in item.items() if method in {'get', 'post', 'put', 'patch', 'delete'}]
    assert len(operations) == 59
    for operation in operations:
        assert operation.get('description'), operation['operationId']
        for parameter in operation.get('parameters', []):
            assert parameter.get('description'), (operation['operationId'], parameter['name'])


def test_every_request_property_has_description():
    schema = create_app().openapi()
    for name in ['Credentials', 'Register', 'EmailCodeRequest', 'PasswordResetCodeRequest', 'PasswordReset',
                 'CourseCreate', 'CoursePatch', 'ProfilePatch',
                 'TaskCreate', 'TaskPatch', 'SubtaskCreate', 'SubtaskEdit', 'SubtaskToggle', 'AttemptCreate', 'Ask', 'Reset', 'QuestionCreate',
                 'NodeCreate', 'EdgeCreate', 'QuestionKnowledgeLink', 'QuestionEvidence', 'PracticeDraftRequest',
                 'GuideAsk', 'CoachRequest', 'CoachConfirm']:
        for field, value in schema['components']['schemas'][name]['properties'].items():
            assert value.get('description'), (name, field)
    node = schema['components']['schemas']['NodeCreate']['properties']
    assert node['x']['maximum'] == 760
    assert '760' in node['x']['description']


def test_every_app_function_has_a_docstring():
    missing = []
    for path in (ROOT / 'app').glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not ast.get_docstring(node):
                missing.append(f'{path.name}:{node.lineno} {node.name}')
    assert not missing, missing


def test_business_responses_have_named_models():
    schema = create_app().openapi()
    for route, item in schema['paths'].items():
        for method, operation in item.items():
            if method not in {'get', 'post', 'put', 'patch', 'delete'}:
                continue
            if route.endswith('/file') or route.endswith('/data/export'):
                continue
            code = '201' if '201' in operation['responses'] else '200'
            if '204' in operation['responses']:
                continue
            response = operation['responses'][code]['content']['application/json']['schema']
            assert '$ref' in response or any('$ref' in item for item in response.get('anyOf', [])), (route, method, response)


def test_response_and_upload_fields_are_described():
    schema = create_app().openapi()
    for name, value in schema['components']['schemas'].items():
        for field, detail in value.get('properties', {}).items():
            assert detail.get('description'), (name, field)
    assert schema['components']['securitySchemes']['HTTPBearer']['description']


def test_generated_reference_matches_current_operations():
    schema = create_app().openapi()
    reference = (ROOT / 'docs/接口参考.md').read_text(encoding='utf-8')
    for route, item in schema['paths'].items():
        for method in item:
            if method in {'get', 'post', 'put', 'patch', 'delete'}:
                assert f'## {method.upper()} {route}' in reference
