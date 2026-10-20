"""Offline service claims, receipt recovery, eligibility, and exact-image text."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import threading
import unittest
from unittest.mock import MagicMock, patch

import server
from media_share import field
import test_media_share


class MediaAssignmentTests(unittest.TestCase):
    tearDown=test_media_share.MediaShareTests.tearDown
    request=test_media_share.MediaShareTests.request
    upload=test_media_share.MediaShareTests.upload
    sync=test_media_share.MediaShareTests.sync

    def setUp(self):
        test_media_share.MediaShareTests.setUp(self)
        self.assignments=server.media_share.assignments
        self.addCleanup(setattr,self.assignments,'now',self.assignments.now)
        self.assignments.now=lambda:datetime(2026,10,21,4,0,tzinfo=timezone.utc)

    def claim(self,row,service='youtube:video1'):
        return dict(serviceId=service,dropId=row['id'],imageKey=row['imageKey'])

    def reserve(self,row,service='youtube:video1'):
        return self.request('post','assignments/reserve',json=self.claim(row,service))

    def get_claims(self,service='youtube:video1'):
        return self.request('get','assignments?serviceId='+service)

    def test_old_unused_and_eastern_date_boundary(self):
        row=self.upload().json
        with server.connect_db() as db:
            db.execute('UPDATE media_share_drops SET created=?,available_starting_date=? WHERE id=?',
                       (1,'2026-10-21',row['id']))
        self.assignments.now=lambda:datetime(2026,10,21,3,59,tzinfo=timezone.utc)
        self.assertEqual(self.reserve(row).status_code,409)
        self.assignments.now=lambda:datetime(2026,10,21,4,0,tzinfo=timezone.utc)
        response=self.reserve(row)
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['assignment']['state'],'reserved')
        # The same date stays eligible later; it is not an expiry.
        later=self.upload('older-no-restrict').json
        with server.connect_db() as db:
            db.execute("UPDATE media_share_drops SET created=1,available_starting_date='2020-01-01' WHERE id=?",(later['id'],))
        self.assertEqual(self.reserve(later,'youtube:video2').status_code,200)

    def test_overlap_allows_exactly_one_service_and_retries_keep_claim(self):
        row=self.upload().json
        barrier=threading.Barrier(2)
        original=self.assignments.source
        def synchronized(ident):
            value=original(ident);barrier.wait(timeout=5);return value
        def worker(service):
            with server.app.test_client() as client:
                return client.post('/api/media-share/assignments/reserve',
                    json=self.claim(row,service),headers={'Authorization':'Bearer test'}).status_code
        with patch.object(server.media_share.identity,'verify',return_value={'sub':'manager'}), \
                patch.object(self.assignments,'source',side_effect=synchronized), ThreadPoolExecutor(2) as pool:
            results=list(pool.map(worker,['youtube:video1','youtube:video2']))
        self.assertEqual(sorted(results),[200,409])
        winner='youtube:video1' if results[0]==200 else 'youtube:video2'
        self.assertEqual(self.reserve(row,winner).status_code,200)
        server.media_share.initialize()
        self.assertEqual(self.reserve(row,winner).status_code,200)
        with server.connect_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM media_share_assignments').fetchone()[0],1)

    def test_partial_destination_receipts_survive_restart_and_are_idempotent(self):
        row=self.upload().json
        self.reserve(row)
        receipt={**self.claim(row),'destination':'youtube','kind':'image',
                 'destinationId':'video1','version':row['imageKey']}
        for _ in range(2):
            response=self.request('post','assignments/used',json=receipt)
            self.assertEqual(response.status_code,200,response.json)
        server.media_share.initialize()
        assignment=self.get_claims().json['assignments'][0]
        self.assertEqual(assignment['state'],'used')
        self.assertEqual(assignment['destinations'],['youtube'])
        self.assertEqual(assignment['receipts'],{'youtube:image:video1':row['imageKey']})
        response=self.request('post','assignments/used',json={**self.claim(row),
            'destination':'planning_center','kind':'title','destinationId':'episode1','version':'title-hash-1'})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['assignment']['receipts']['planning_center:title:episode1'],'title-hash-1')
        self.assertEqual(self.reserve(row,'youtube:other').status_code,409)

    def test_delayed_empty_failed_and_replaced_text_are_distinct(self):
        row=self.upload().json
        self.reserve(row)
        self.assertEqual(self.get_claims().json['assignments'][0]['drop']['titleStatus'],'queued')
        # A successful extraction with no headline is terminal, not pending.
        with server.connect_db() as db:
            db.execute("UPDATE media_share_drops SET title_status='complete',title=NULL,title_revision=1 WHERE id=?",(row['id'],))
        completed=self.get_claims().json['assignments'][0]['drop']
        self.assertEqual(completed['titleStatus'],'complete')
        self.assertIsNone(completed['autoTitle'])
        with server.connect_db() as db:
            db.execute("UPDATE media_share_drops SET title_status='error' WHERE id=?",(row['id'],))
        self.assertEqual(self.get_claims().json['assignments'][0]['drop']['titleStatus'],'error')
        pending=self.upload('second-pending-slide').json
        self.reserve(pending,'youtube:video2')
        replacement=self.upload('replace-pending-slide',prior=pending).json
        old=self.get_claims('youtube:video2').json['assignments'][0]
        self.assertEqual(old['imageKey'],pending['imageKey'])
        self.assertEqual(old['drop']['titleStatus'],'superseded')
        self.assertNotEqual(old['imageKey'],replacement['imageKey'])
        self.assertEqual(self.reserve(pending,'youtube:other').status_code,409)

    def test_new_upload_does_not_rebind_title_and_deleted_pending_is_terminal(self):
        original=self.upload().json
        self.reserve(original)
        self.upload('unrelated-new-slide')
        with server.connect_db() as db:
            db.execute("UPDATE media_share_drops SET title_status='complete',title='Original title',title_revision=1 WHERE id=?",(original['id'],))
        assignment=self.get_claims().json['assignments'][0]
        self.assertEqual(assignment['imageKey'],original['imageKey'])
        self.assertEqual(assignment['drop']['autoTitle'],'Original title')
        pending=self.upload('delete-pending-slide').json
        self.reserve(pending,'youtube:video2')
        self.request('delete','drops/'+pending['id'],json={'revision':pending['revision']})
        assignment=self.get_claims('youtube:video2').json['assignments'][0]
        self.assertEqual(assignment['drop']['titleStatus'],'unavailable')
        self.assertEqual(assignment['state'],'reserved')

    def test_legacy_firestore_date_is_verified_not_supplied_by_client(self):
        row=dict(id='legacy-drop',imageKey='legacy-image')
        values=dict(kind='drop',images=[dict(key=row['imageKey'],chunks=1)],
                    availableStartingDate='2026-10-22',createdAt='2020-01-01T00:00:00Z')
        response=MagicMock();response.status_code=200
        response.__enter__.return_value=response
        response.json.side_effect=lambda:{'fields':{key:field(value) for key,value in values.items()}}
        with patch('media_share_assignments.requests.get',return_value=response) as get:
            rejected=self.request('post','assignments/reserve',json={**self.claim(row),'availableStartingDate':''})
            self.assertEqual(rejected.status_code,409)
            values['availableStartingDate']=''
            accepted=self.reserve(row)
        self.assertEqual(accepted.status_code,200,accepted.json)
        self.assertTrue(get.call_args.args[0].endswith('/legacy-drop'))
        self.assertEqual(get.call_args.kwargs['headers']['Authorization'],'Bearer test')

    def test_retention_never_removes_claims_and_unclaimed_receipt_rejected(self):
        row=self.upload().json
        denied=self.request('post','assignments/used',json={**self.claim(row),'destination':'youtube'})
        self.assertEqual(denied.status_code,409)
        self.reserve(row)
        with server.connect_db() as db:
            db.execute('UPDATE media_share_drops SET updated=0,published=revision WHERE id=?',(row['id'],))
        self.sync()
        with server.connect_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM media_share_drops').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM media_share_assignments').fetchone()[0],1)
        self.assertEqual(self.reserve(row).status_code,200)

    def test_replacement_preserves_claimed_chunks_for_failed_destination_retry(self):
        original=self.upload().json
        self.reserve(original)
        self.sync()
        self.upload('replace-claimed-image',prior=original)
        self.sync()
        with patch.object(server.media_share.publisher,'write') as delete:
            server.media_share.sync_once()
        delete.assert_not_called()
        with server.connect_db() as db:
            self.assertEqual(db.execute('SELECT key FROM media_share_garbage').fetchone()[0],original['imageKey'])


if __name__=='__main__':unittest.main()
