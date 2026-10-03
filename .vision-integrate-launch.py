"""Resolve only the reviewed launch/document merge and connect guest job cleanup."""
from pathlib import Path
import re


def edit(path, old, new):
    p = Path(path)
    text = p.read_text()
    assert text.count(old) == 1, (path, old[:100], text.count(old))
    p.write_text(text.replace(old, new, 1))


def conflicts(path, select):
    p = Path(path)
    text = p.read_text()
    pattern = r'^<<<<<<<[^\n]*\n([\s\S]*?)^=======\n([\s\S]*?)^>>>>>>>[^\n]*\n'
    text, n = re.subn(pattern, lambda m: select(m[1], m[2]), text, flags=re.M)
    assert n, 'Expected reviewed merge conflict: ' + path
    assert not re.search(r'^(<<<<<<<|=======|>>>>>>>)', text, re.M)
    p.write_text(text)


conflicts('web/build.py', lambda a, b: (a if "'documents.js'" in a else b).replace("'projects.js', 'auth.js'", "'projects.js', 'launch.js', 'auth.js'"))
conflicts('vision-pc/Setup-Vision-PC.ps1', lambda a, b: (a if "'documents.py'" in a else b).replace("'server.py','media.py'", "'server.py','trials.py','media.py'"))


def server_conflict(a, b):
    if 'documents.initialize' in a+b:
        docs, guest = (a, b) if 'documents.initialize' in a else (b, a)
        return docs + guest
    if "capabilities={" in a+b:
        return (a if 'documentProcessing' in a else b).replace("'documentProcessing': document['ready']}", "'documentProcessing': document['ready'], 'temporarySessions': trials.enabled()}")
    if 'DocumentJobs(' in a+b:
        docs, guest = (a, b) if 'DocumentJobs(' in a else (b, a)
        return docs + guest.replace('uploaded_media, YOUTUBE_WORKER_LOCK)', 'uploaded_media, YOUTUBE_WORKER_LOCK, documents=documents)')
    raise AssertionError('Unreviewed server conflict')


conflicts('vision-pc/server.py', server_conflict)
edit('vision-pc/trials.py', 'def __init__(self, app, db, error, sessions, transcriptions, uploaded_media, youtube_lock):',
     'def __init__(self, app, db, error, sessions, transcriptions, uploaded_media, youtube_lock, documents=None):')
edit('vision-pc/trials.py', '        self.youtube_lock = youtube_lock', '        self.youtube_lock, self.documents = youtube_lock, documents')
edit('vision-pc/trials.py', '(?:media|transcriptions|runs)', '(?:media|transcriptions|runs|documents)')
edit('vision-pc/trials.py', "                db.execute('UPDATE project_runs SET cancel_requested=1 WHERE project_id=?', (pid,))", """                db.execute('UPDATE project_runs SET cancel_requested=1 WHERE project_id=?', (pid,))
                if self.documents is not None:
                    db.execute("UPDATE document_jobs SET cancel_requested=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE project_id=?", (pid,))""")
edit('vision-pc/trials.py', '                    locks = (self.sessions.worker_lock, self.transcriptions._worker_lock, self.uploaded_media.worker_lock, self.youtube_lock)',
     '                    locks = (self.sessions.worker_lock, self.transcriptions._worker_lock, self.uploaded_media.worker_lock, self.youtube_lock) + ((self.documents.worker_lock,) if self.documents is not None else ())')
edit('vision-pc/trials.py', "                        for row in db.execute('SELECT id FROM project_runs WHERE project_id=?', (pid,)).fetchall():", """                        if self.documents is not None:
                            for row in db.execute('SELECT id FROM document_jobs WHERE project_id=?', (pid,)).fetchall():
                                self.remove_directory(self.documents.root / row['id'])
                            db.execute('DELETE FROM document_jobs WHERE project_id=?', (pid,))
                        for row in db.execute('SELECT id FROM project_runs WHERE project_id=?', (pid,)).fetchall():""")
edit('vision-pc/documents.py', '                value = dict(row)\n', """                value = dict(row)
                trials = self.app.config.get('TRIALS')
                if trials and trials.expired(value['project_id']):
                    db.execute("UPDATE document_jobs SET status='cancelled',cancel_requested=1,phase='Trial ended' WHERE id=?", (value['id'],))
                    return True
""")
edit('web/documents.js', "typeof accountReady==='undefined'||!projectAccountUID()", "typeof accountReady==='undefined'||(!projectAccountUID()&&!(typeof trialActive==='function'&&trialActive()))")
edit('web/projects.js', "  if(typeof cancelBoardImports==='function')cancelBoardImports();", """  if(typeof cancelBoardImports==='function')cancelBoardImports();
  if(typeof documentWorkers!=='undefined'){for(const r of documentWorkers.values())r.controller.abort();documentWorkers.clear();}""")
edit('web/launch.js', "let trialSession=null,trialStarting=false,trialTimer=0,trialFinishing=null;", "let trialSession=null,trialStarting=false,trialTimer=0,trialFinishing=null;\nfunction trialRemaining(){return trialSession?Math.max(0,Math.min(trialSession.until-performance.now(),trialSession.wallUntil-Date.now())):0;}")
edit('web/launch.js', "performance.now()<trialSession.until", "trialRemaining()>0")
edit('web/launch.js', "Math.ceil((trialSession.until-performance.now())/1000)", "Math.ceil(trialRemaining()/1000)")
edit('web/launch.js', "until:performance.now()+remaining*1000", "until:performance.now()+remaining*1000,wallUntil:Date.now()+remaining*1000")
edit('web/GUEST-TRIAL.md', 'uploaded videos, direct YouTube links', 'uploaded videos, document extraction, direct YouTube links')

p=Path('vision-pc/test_trials.py')
s=p.read_text();needle="    def test_early_signin_end_revokes_and_does_not_reset(self):"
assert s.count(needle)==1
s=s.replace(needle,'''    def test_guest_document_jobs_are_isolated_expire_and_remove_temporary_files(self):
        _, lease = self.start()
        _, other = self.start('b', '198.51.100.2')
        base = '/api/projects/' + lease['project']['id'] + '/documents'
        with patch.object(server.documents, 'capability', return_value={'ready': True}):
            status, job = self.call('POST', base, headers=self.headers(lease),
                data={'requestId': 'doc-trial', 'file': (io.BytesIO(b'Temporary document'), 'trial.txt')})
        self.assertEqual(status, 202, job)
        directory = server.documents.root / job['id']
        self.assertTrue((directory / 'source').exists())
        self.assertEqual(self.call('GET', base + '/' + job['id'], headers=self.headers(other))[0], 403)
        self.assertEqual(self.call('GET', base, headers=self.headers(lease))[0], 200)
        self.expire(lease)
        with patch.object(server.documents, 'capability', return_value={'ready': True}), \\
             patch.object(server.documents, 'process') as processor:
            server.documents.work_once()
            processor.assert_not_called()
        with server.documents.worker_lock:
            server.trials.sweep()
            self.assertTrue(directory.exists(), 'Do not delete while a document writer holds the lock')
        server.trials.sweep()
        self.assertFalse(directory.exists())
        with server.connect_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM document_jobs').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 0)

'''+needle)
p.write_text(s)
p=Path('tests/launch-trial-smoke.cjs');s=p.read_text()
s=s.replace("signOut:()=>accountGoogleSignOut()", "signOut:()=>accountGoogleSignOut(),collectDocuments:()=>collectDocumentSources()")
s=s.replace("uploadedMedia:true}})", "uploadedMedia:true,documentProcessing:true},documentProcessing:{ready:true}})")
s=s.replace("   if(u.pathname.endsWith('/transcriptions'))", """   if(u.pathname.includes('/documents/request/'))return json(route,{error:'No receipt yet'},404);
   if(u.pathname.endsWith('/documents')&&req.method()==='POST')return json(route,{id:'d'.repeat(24),status:'queued',sourceSha256:'e'.repeat(64)},202);
   if(u.pathname.includes('/documents/'))return json(route,{id:'d'.repeat(24),status:'processing',phase:'Processing document'});
   if(u.pathname.endsWith('/transcriptions'))""")
needle="  const expiry=await page.evaluate(()=>window.__trialTest.session().expiresAt);"
assert needle in s
s=s.replace(needle,"""  await page.evaluate(()=>{
   const n=window.__trialTest.state().nodes[0];n.attachments.push({id:'trial-doc-file',name:'trial.txt',mime:'text/plain',data:'data:text/plain;base64,VGVtcG9yYXJ5',size:9,generated:false});
   window.__trialTest.collectDocuments();
   if(!n.attachments.find(a=>a.id==='trial-doc-file').documentJob)throw new Error('Guest document was not scheduled');
  });
  await page.waitForFunction(()=>window.__trialTest.state().nodes[0].attachments.find(a=>a.id==='trial-doc-file').documentJob.id==='d'.repeat(24));
  assert.ok(requests.some(r=>r.path.endsWith('/documents')&&r.method==='POST'&&r.auth.startsWith('Bearer trial_')));
"""+needle)
s=s.replace('trial processing without saved project;', 'trial processing and document extraction without saved project;')
p.write_text(s)
print('Preserved document update; integrated isolated document jobs and cleanup into guest trial.')
