import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TEST_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_debug.db')
os.environ['MAINTENANCE_WINDOW_DB_PATH'] = TEST_DB_PATH
if os.path.exists(TEST_DB_PATH):
    os.remove(TEST_DB_PATH)

import importlib
for mod in list(sys.modules.keys()):
    if mod.startswith('app.') or mod == 'main':
        del sys.modules[mod]

import app.database as db_mod
db_mod.DB_PATH = TEST_DB_PATH
from sqlalchemy import create_engine
db_mod.engine = create_engine(f'sqlite:///{TEST_DB_PATH}', connect_args={'check_same_thread': False})
from sqlalchemy.orm import sessionmaker
db_mod.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_mod.engine)

from fastapi.testclient import TestClient
from main import app
from app.database import Base, engine as e
Base.metadata.create_all(bind=e)

client = TestClient(app)

# 准备
r = client.post('/environments', json={'name': 'env-test', 'description': ''})
env_id = r.json()['id']

r = client.post('/roles', json={'name': 'CM', 'can_approve': 1, 'description': ''})
role_mgr_id = r.json()['id']

r = client.post('/roles', json={'name': 'DEV', 'can_approve': 0, 'description': ''})
role_dev_id = r.json()['id']

r = client.post('/users', json={'username': 'mgr', 'display_name': 'Mgr', 'role_id': role_mgr_id})
mgr_id = r.json()['id']

r = client.post('/users', json={'username': 'dev', 'display_name': 'Dev', 'role_id': role_dev_id})
dev_id = r.json()['id']

r = client.post('/window-templates', json={
    'name': 'TestTpl',
    'environment_id': env_id,
    'start_time': '02:00',
    'end_time': '04:00',
    'is_shared': 1,
    'creator_id': dev_id,
})
tpl_id = r.json()['id']

r = client.post('/schedule-plans', json={
    'name': 'TestPlan',
    'template_id': tpl_id,
    'generate_mode': 'specific_dates',
    'specific_dates': ['2026-07-01'],
    'creator_id': dev_id,
})
plan_id = r.json()['id']
print(f'Created plan: {plan_id}, status: {r.json()["status"]}')

r = client.post(f'/schedule-plans/{plan_id}/submit', json={'operator_id': dev_id})
print(f'Submit response: {r.status_code}, {r.text[:500]}')

r = client.post(f'/schedule-plans/{plan_id}/reject', json={'operator_id': mgr_id, 'reason': 'test reject'})
print(f'Reject response: {r.status_code}, {r.text[:500]}')
