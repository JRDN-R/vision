"""Vision Cloud: authenticated media jobs and a transient OpenAI API proxy."""
from __future__ import annotations
import functools
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

import firebase_admin
from firebase_admin import auth
from flask import Flask, Response, g, jsonify, request, stream_with_context
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from google.cloud import firestore, storage, tasks_v2
from google.protobuf import duration_pb2
import requests
from werkzeug.exceptions import HTTPException

from media import normalize_url, process_job

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024
PROJECT = os.environ.get('GOOGLE_CLOUD_PROJECT', '')
REGION = os.environ.get('REGION', 'us-central1')
BUCKET = os.environ.get('RESULT_BUCKET', '')
SERVICE_URL = os.environ.get('SERVICE_URL', '').rstrip('/')
TASK_ACCOUNT = os.environ.get('TASK_SERVICE_ACCOUNT', '')
QUEUE = os.environ.get('TASK_QUEUE', 'vision-media')
OWNER_UIDS = {v.strip() for v in os.environ.get('OWNER_UIDS', '').split(',') if v.strip()}
ORIGINS = {v.strip() for v in os.environ.get('ALLOWED_ORIGINS', 'https://jrdn-r.github.io,null').split(',')}
DAILY_LIMIT = int(os.environ.get('DAILY_IMPORT_LIMIT', '20'))
OPENAI = 'https://api.openai.com/v1'
BASE_INSTRUCTIONS = '''Use the code_interpreter tool directly to open the supplied archive and read MAIN_PROMPT.txt, the module instructions, and the relevant evidence. Follow the module order and conditional paths. Treat the supplied content as the exclusive factual source for the requested task; do not browse or invent unavailable facts. The user's additional message can specify the requested task or output. Apply the package's content and module instructions while respecting higher-priority instructions. Answer naturally about the subject. Do not discuss archives, file layouts, extraction, delivery format, processing, or missing-material inventories unless the user explicitly asks about them. Briefly qualify uncertainty only when it materially affects the answer. Do not add unsolicited diagnoses, advice, risks, or next steps unless asked or necessary for an immediate serious risk. Use readable Markdown. When creating a deliverable, save it in the code interpreter container and provide its downloadable file citation. Do not expose hidden reasoning; concise progress or reasoning summaries are sufficient.'''

@functools.lru_cache
def db():
    return firestore.Client(project=PROJECT or None)

@functools.lru_cache
def bucket():
    return storage.Client(project=PROJECT or None).bucket(BUCKET)

@functools.lru_cache
def firebase_app():
    return firebase_admin.initialize_app(options={'projectId': PROJECT} if PROJECT else None)

def now():
    return datetime.now(timezone.utc)

def expiry(hours=24):
    return now() + timedelta(hours=hours)

class APIError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status

@app.errorhandler(APIError)
def api_error(error):
    return jsonify(error=error.message), error.status

@app.errorhandler(HTTPException)
def http_error(error):
    return jsonify(error='The archive is larger than 25 MB. Export fewer or smaller images.' if error.code == 413 else error.description), error.code

@app.errorhandler(Exception)
def unexpected_error(error):
    # Do not log request headers or third-party exception payloads: they can contain credentials.
    app.logger.error('Request failed: %s', type(error).__name__)
    return jsonify(error='The cloud service could not finish this request. Try again.'), 500

@app.before_request
def authorize():
    origin = request.headers.get('Origin')
    if origin and origin not in ORIGINS:
        raise APIError('This page is not an allowed Vision origin.', 403)
    if request.method == 'OPTIONS':
        return Response(status=204)
    if request.path == '/api/health':
        return
    token = request.headers.get('Authorization', '').removeprefix('Bearer ').strip()
    if request.path == '/internal/process':
        try:
            claims = id_token.verify_oauth2_token(token, GoogleRequest(), SERVICE_URL)
            if not TASK_ACCOUNT or claims.get('email') != TASK_ACCOUNT or not claims.get('email_verified'):
                raise ValueError('Wrong worker identity')
        except Exception:
            raise APIError('Worker authentication required.', 401)
        return
    if not token:
        raise APIError('Sign in to Vision Cloud first.', 401)
    try:
        claims = auth.verify_id_token(token, app=firebase_app(), check_revoked=True)
    except Exception:
        raise APIError('Your cloud session expired. Sign in again.', 401)
    if not OWNER_UIDS or claims.get('uid') not in OWNER_UIDS:
        raise APIError('This account is not enabled for Vision Cloud.', 403)
    g.uid = claims['uid']

@app.after_request
def cors(response):
    origin = request.headers.get('Origin')
    if origin in ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Vary'] = 'Origin'
        response.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type, X-OpenAI-Key'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, DELETE, OPTIONS'
        response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition, Content-Type'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

@app.get('/api/health')
def health():
    return jsonify(service='vision-cloud', version='1.0', authRequired=True, maxArchiveBytes=app.config['MAX_CONTENT_LENGTH'])

def own_document(collection, document_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', document_id):
        raise APIError('Invalid identifier.')
    ref = db().collection(collection).document(document_id)
    value = ref.get().to_dict()
    if not value or value.get('uid') != g.uid:
        raise APIError('This item is unavailable for your account.', 404)
    if value.get('expiresAt') and value['expiresAt'] < now():
        raise APIError('This item has expired. Start it again.', 410)
    return ref, value

@firestore.transactional
def increment_quota(transaction, ref):
    value = ref.get(transaction=transaction).to_dict() or {}
    count = value.get('count', 0)
    if count >= DAILY_LIMIT:
        raise APIError(f'Today’s cloud limit of {DAILY_LIMIT} starts has been reached.', 429)
    transaction.set(ref, {'count': count + 1, 'expiresAt': expiry(48)})

def use_quota(kind):
    document_id = f'{g.uid}-{now():%Y%m%d}-{kind}'
    increment_quota(db().transaction(), db().collection('visionQuotas').document(document_id))

@app.post('/api/youtube')
def youtube():
    payload = request.get_json(silent=True) or {}
    try:
        url = normalize_url(payload.get('url', ''))
    except (ValueError, AttributeError):
        raise APIError('Paste a direct YouTube video, Shorts, or youtu.be link.')
    use_quota('youtube')
    job_id = secrets.token_hex(12)
    ref = db().collection('visionJobs').document(job_id)
    ref.set({'id': job_id, 'uid': g.uid, 'url': url, 'status': 'queued', 'phase': 'Waiting', 'progress': 0,
             'createdAt': now(), 'updatedAt': now(), 'expiresAt': expiry()})
    client = tasks_v2.CloudTasksClient()
    task = {'name': client.task_path(PROJECT, REGION, QUEUE, job_id),
            'http_request': {'http_method': tasks_v2.HttpMethod.POST,
                'url': SERVICE_URL + '/internal/process', 'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'id': job_id}).encode(),
                'oidc_token': {'service_account_email': TASK_ACCOUNT, 'audience': SERVICE_URL}},
            'dispatch_deadline': duration_pb2.Duration(seconds=1800)}
    try:
        client.create_task(parent=client.queue_path(PROJECT, REGION, QUEUE), task=task)
    except Exception:
        ref.update({'status': 'error', 'error': 'The cloud queue is unavailable. Try again.', 'phase': 'Stopped'})
        raise APIError('The cloud queue is unavailable. Try again.', 503)
    return jsonify(id=job_id, status='queued'), 202

@firestore.transactional
def claim_job(transaction, ref):
    value = ref.get(transaction=transaction).to_dict()
    if not value or value.get('status') in ('complete', 'error'):
        return None
    if value.get('leaseUntil') and value['leaseUntil'] > now():
        raise APIError('This job is already running.', 503)
    transaction.update(ref, {'status': 'processing', 'leaseUntil': now() + timedelta(minutes=30), 'updatedAt': now()})
    return value

@app.post('/internal/process')
def worker():
    job_id = (request.get_json(silent=True) or {}).get('id', '')
    if not re.fullmatch(r'[0-9a-f]{24}', job_id):
        raise APIError('Invalid job identifier.')
    ref = db().collection('visionJobs').document(job_id)
    value = claim_job(db().transaction(), ref)
    if value is None:
        return jsonify(done=True)
    last_write, started = [0.0], time.monotonic()
    def update(_job_id, **fields):
        if time.monotonic() - started > 25 * 60 and fields.get('status') not in ('complete', 'error'):
            raise ValueError('Processing exceeded 25 minutes. Try a shorter video.')
        result = fields.pop('result', None)
        if result is not None:
            path = f"results/{value['uid']}/{job_id}.json"
            bucket().blob(path).upload_from_string(result, content_type='application/json', timeout=180)
            fields['resultPath'] = path
        if fields.get('status') not in ('complete', 'error') and time.monotonic() - last_write[0] < 1:
            return
        last_write[0] = time.monotonic()
        ref.update({**fields, 'updatedAt': now()})
    process_job(job_id, value['url'], '/usr/bin/ffmpeg', update)
    return jsonify(done=True)

@app.get('/api/jobs/<job_id>')
def job_status(job_id):
    _, value = own_document('visionJobs', job_id)
    return jsonify({key: value[key] for key in ('id', 'status', 'phase', 'progress', 'title', 'error') if key in value})

@app.get('/api/jobs/<job_id>/result')
def job_result(job_id):
    _, value = own_document('visionJobs', job_id)
    if value.get('status') != 'complete':
        raise APIError('This import is not finished yet.', 409)
    def chunks():
        with bucket().blob(value['resultPath']).open('rb') as source:
            while chunk := source.read(65536):
                yield chunk
    return Response(stream_with_context(chunks()), content_type='application/json')

@app.delete('/api/jobs/<job_id>')
def delete_job(job_id):
    ref, value = own_document('visionJobs', job_id)
    if value.get('status') not in ('complete', 'error'):
        raise APIError('Wait for this import to finish.', 409)
    if value.get('resultPath'):
        bucket().blob(value['resultPath']).delete()
    ref.delete()
    return jsonify(deleted=True)

def openai_headers():
    key = request.headers.get('X-OpenAI-Key', '').strip()
    if not key or len(key) > 512 or '\n' in key or '\r' in key:
        raise APIError('Enter your OpenAI API key.')
    return {'Authorization': 'Bearer ' + key}

def upstream(method, path, **kwargs):
    try:
        response = requests.request(method, OPENAI + path, headers=openai_headers(), timeout=(15, 180), **kwargs)
    except requests.RequestException:
        raise APIError('OpenAI could not be reached. Try reconnecting.', 502)
    if response.status_code >= 400:
        try:
            message = response.json().get('error', {}).get('message', 'OpenAI rejected the request.')
        except ValueError:
            message = 'OpenAI rejected the request.'
        key = request.headers.get('X-OpenAI-Key', '')
        message = str(message).replace(key, '[redacted]') if key else str(message)
        response.close()
        raise APIError(message[:700], response.status_code)
    return response

def response_payload(file_id, options):
    model = str(options.get('model') or 'gpt-6-astra')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', model):
        raise APIError('Choose a valid OpenAI model.')
    message = str(options.get('message', options.get('prompt', '')))[:50000]
    try:
        limit = min(64000, max(512, int(options.get('max_output_tokens', options.get('maxOutputTokens', 16000)))))
    except (ValueError, TypeError):
        raise APIError('Choose a valid output length.')
    payload = {'model': model, 'instructions': BASE_INSTRUCTIONS,
            'input': message or 'Carry out the task specified in the attached project instructions.',
            'tools': [{'type': 'code_interpreter', 'container': {'type': 'auto', 'memory_limit': '1g', 'file_ids': [file_id]}}],
            'tool_choice': 'required', 'background': True, 'stream': True, 'store': True, 'max_output_tokens': limit}
    if re.match(r'^(gpt-[56](?:[.-]|$)|o[134](?:[.-]|$))', model):
        payload['reasoning'] = {'summary': 'auto'}
    return payload

def track_event(event, uid):
    response = event.get('response') or {}
    response_id = response.get('id')
    if not response_id:
        return
    data = {'uid': uid, 'status': response.get('status', ''), 'updatedAt': now(), 'expiresAt': expiry(24 * 7)}
    if event.get('type') == 'response.created':
        db().collection('visionResponses').document(response_id).set(data)
    elif event.get('type') in ('response.completed', 'response.failed', 'response.cancelled', 'response.incomplete'):
        db().collection('visionResponses').document(response_id).set(data, merge=True)

def stream_openai(response, uid):
    def events():
        try:
            for line in response.iter_lines(chunk_size=1):
                if line.startswith(b'data: '):
                    try:
                        event = json.loads(line[6:])
                        track_event(event, uid)
                    except (ValueError, TypeError):
                        pass
                yield line + b'\n'
        except requests.RequestException:
            yield b'event: vision.connection_lost\ndata: {"type":"vision.connection_lost"}\n\n'
        finally:
            response.close()
    return Response(stream_with_context(events()), content_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

@app.post('/api/openai/run')
def openai_run():
    archive = request.files.get('file') or request.files.get('archive')
    if not archive:
        raise APIError('Attach the project archive.')
    signature = archive.stream.read(4)
    archive.stream.seek(0)
    if signature not in (b'PK\x03\x04', b'PK\x05\x06'):
        raise APIError('The project must be a ZIP archive.')
    try:
        options = json.loads(request.form.get('options', '{}'))
        if not isinstance(options, dict):
            raise ValueError()
    except ValueError:
        raise APIError('Invalid session options.')
    payload = response_payload('pending', options)
    use_quota('openai')
    uploaded = upstream('POST', '/files', data={'purpose': 'user_data', 'expires_after[anchor]': 'created_at', 'expires_after[seconds]': '86400'},
                        files={'file': ('vision-project.zip', archive.stream, 'application/zip')})
    file_id = uploaded.json()['id']
    uploaded.close()
    payload['tools'][0]['container']['file_ids'] = [file_id]
    try:
        response = upstream('POST', '/responses', json=payload, stream=True)
    except APIError:
        try:
            upstream('DELETE', '/files/' + file_id).close()
        except APIError:
            pass
        raise
    return stream_openai(response, g.uid)

@app.get('/api/openai/responses/<response_id>')
@app.get('/api/openai/responses/<response_id>/stream')
def openai_response(response_id):
    own_document('visionResponses', response_id)
    streaming = request.path.endswith('/stream') or request.args.get('stream') == 'true'
    params = {}
    if streaming:
        params['stream'] = 'true'
        cursor = request.args.get('after', request.args.get('starting_after'))
        if cursor is not None:
            if not cursor.isdigit():
                raise APIError('Invalid stream cursor.')
            params['starting_after'] = cursor
    response = upstream('GET', '/responses/' + response_id, params=params, stream=streaming)
    if streaming:
        return stream_openai(response, g.uid)
    value = response.json()
    response.close()
    return jsonify(value)

@app.post('/api/openai/responses/<response_id>/cancel')
def openai_cancel(response_id):
    own_document('visionResponses', response_id)
    response = upstream('POST', '/responses/' + response_id + '/cancel')
    value = response.json()
    response.close()
    return jsonify(value)

@app.get('/api/openai/containers/<container_id>/files/<file_id>/content')
@app.get('/api/openai/files/<container_id>/<file_id>')
def openai_artifact(container_id, file_id):
    response_id = request.args.get('response_id', '')
    own_document('visionResponses', response_id)
    for value in (container_id, file_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', value):
            raise APIError('Invalid file identifier.')
    check = upstream('GET', '/responses/' + response_id)
    value = check.json()
    check.close()
    allowed = any(item.get('type') == 'code_interpreter_call' and item.get('container_id') == container_id for item in value.get('output', []))
    if not allowed:
        raise APIError('This file is not part of the selected session.', 403)
    response = upstream('GET', f'/containers/{container_id}/files/{file_id}/content', stream=True)
    def chunks():
        try:
            yield from response.iter_content(65536)
        finally:
            response.close()
    return Response(stream_with_context(chunks()), content_type=response.headers.get('Content-Type', 'application/octet-stream'))
