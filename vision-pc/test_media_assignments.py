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

    def test_exact_service_day_and_eastern_date_boundary(self):
        row=self.upload().json
        with server.connect_db() as db:
            db.execute('UPDATE media_share_drops SET created=?,available_starting_date=? WHERE id=?',
                       (1,'2026-10-21',row['id']))
        self.assignments.now=lambda:datetime(2026,10,21,3,59,tzinfo=timezone.utc)
        self.assertEqual(self.reserve_at(row,'2026-10-21T19:30:00-04:00').status_code,409)
        self.assignments.now=lambda:datetime(2026,10,21,4,0,tzinfo=timezone.utc)
        response=self.reserve_at(row,'2026-10-21T19:30:00-04:00')
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['assignment']['state'],'reserved')
        # Only undated older uploads remain unrestricted fallbacks.
        later=self.upload('older-no-restrict').json
        with server.connect_db() as db:
            db.execute("UPDATE media_share_drops SET created=1,available_starting_date='2020-01-01' WHERE id=?",(later['id'],))
        self.assertEqual(self.reserve_at(later,'2026-10-21T19:30:00-04:00','youtube:video2').status_code,409)
        with server.connect_db() as db:
            db.execute("UPDATE media_share_drops SET available_starting_date='' WHERE id=?",(later['id'],))
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

    def sunday_row(self,period='pm'):
        row=self.upload().json
        with server.connect_db() as db:
            db.execute("UPDATE media_share_drops SET available_starting_date='2026-10-25',target_service_period=? WHERE id=?",
                       (period,row['id']))
        self.assignments.now=lambda:datetime(2026,10,25,13,tzinfo=timezone.utc)
        return row

    def reserve_at(self,row,start,service='youtube:video1'):
        return self.request('post','assignments/reserve',json={**self.claim(row,service),'serviceStart':start})

    def test_sunday_slot_uses_service_time_not_current_time_and_survives_restart(self):
        row=self.sunday_row('pm')
        self.assertEqual(self.reserve_at(row,'2026-10-25T10:30:00-04:00').status_code,409)
        # PM preparation is valid while the wall clock is still AM.
        accepted=self.reserve_at(row,'2026-10-25T18:00:00-04:00')
        self.assertEqual(accepted.status_code,200,accepted.json)
        assignment=accepted.json['assignment']
        self.assertEqual(assignment['drop']['targetServicePeriod'],'pm')
        self.assertEqual(assignment['serviceStart'],'2026-10-25T18:00:00-04:00')
        server.media_share.initialize()
        # Equivalent instants are canonicalized; a different context is rejected.
        self.assertEqual(self.reserve_at(row,'2026-10-25T22:00:00Z').status_code,200)
        self.assertEqual(self.reserve_at(row,'2026-10-25T10:30:00-04:00').status_code,409)
        self.assertEqual(self.get_claims().json['assignments'][0]['serviceStart'],'2026-10-25T18:00:00-04:00')
        self.assertEqual(self.reserve_at(row,'2026-10-25T18:00:00-04:00','youtube:other').status_code,409)

    def test_opposite_period_and_other_service_dates_are_excluded(self):
        row=self.sunday_row('am')
        self.assignments.now=lambda:datetime(2026,10,26,1,tzinfo=timezone.utc)  # Sunday evening Eastern.
        self.assertEqual(self.reserve_at(row,'2026-10-25T18:00:00-04:00').status_code,409)
        self.assignments.now=lambda:datetime(2026,10,26,14,tzinfo=timezone.utc)
        accepted=self.reserve_at(row,'2026-10-26T18:00:00-04:00')
        self.assertEqual(accepted.status_code,409,accepted.json)

    def test_date_only_target_requires_service_context_and_rejects_other_days(self):
        row=self.upload().json
        with server.connect_db() as db:
            db.execute("UPDATE media_share_drops SET available_starting_date='2026-10-19' WHERE id=?",(row['id'],))
        self.assertEqual(self.reserve(row).status_code,409)
        self.assertEqual(self.reserve_at(row,'2026-10-21T19:30:00-04:00').status_code,409)
        # A retry for the original service day remains valid even after midnight.
        accepted=self.reserve_at(row,'2026-10-19T19:30:00-04:00')
        self.assertEqual(accepted.status_code,200,accepted.json)
        self.assertEqual(self.reserve_at(row,'2026-10-19T23:30:00Z').status_code,200)

    def test_legacy_wrong_day_claim_stays_in_history_but_cannot_be_reserved_again(self):
        row=self.upload().json
        self.reserve_at(row,'2026-10-21T19:30:00-04:00')
        with server.connect_db() as db:
            snapshot=json.loads(db.execute('SELECT snapshot FROM media_share_assignments').fetchone()[0])
            snapshot['availableStartingDate']='2026-10-19'
            db.execute('UPDATE media_share_assignments SET snapshot=?',(json.dumps(snapshot),))
            db.execute("UPDATE media_share_drops SET available_starting_date='2026-10-19' WHERE id=?",(row['id'],))
        self.assertEqual(self.reserve_at(row,'2026-10-21T19:30:00-04:00').status_code,409)
        self.assertEqual(len(self.get_claims().json['assignments']),1)
        self.assertEqual(self.reserve_at(row,'2026-10-19T19:30:00-04:00','youtube:other').status_code,409)

    def test_manual_override_allows_other_date_and_explicit_service_reuse(self):
        row=self.sunday_row('pm')
        payload={**self.claim(row),'serviceStart':'2026-10-21T19:30:00-04:00','selectionMode':'manual'}
        self.assignments.now=lambda:datetime(2026,10,21,20,tzinfo=timezone.utc)
        response=self.request('post','assignments/reserve',json=payload)
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['assignment']['selectionMode'],'manual')
        server.media_share.initialize()
        self.assertEqual(self.get_claims().json['assignments'][0]['selectionMode'],'manual')
        self.assertEqual(self.request('post','assignments/reserve',json=payload).status_code,200)
        self.assertEqual(self.request('post','assignments/reserve',json={**payload,'serviceId':'youtube:other'}).status_code,200)
        self.assertEqual(self.request('post','assignments/reserve',json={**payload,'selectionMode':'auto'}).status_code,409)

    def test_picker_availability_is_read_only_and_hides_other_service_identity(self):
        row=self.upload().json
        self.reserve(row)
        response=self.request('post','assignments/availability',json={'serviceId':'youtube:other',
            'imageKeys':[row['imageKey'],'unused-key']})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['availability'],[
            {'imageKey':row['imageKey'],'selectable':False,'manualSelectable':True,'state':'reserved'},
            {'imageKey':'unused-key','selectable':True,'manualSelectable':True,'state':'unused'}])
        own=self.request('post','assignments/availability',json={'serviceId':'youtube:video1','imageKeys':[row['imageKey']]})
        self.assertTrue(own.json['availability'][0]['selectable'])
        with server.connect_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM media_share_assignments').fetchone()[0],1)
        for invalid in [[], {'serviceId':'youtube:other','imageKeys':['x']*201},
                        {'serviceId':'youtube:other','imageKeys':[1]}]:
            self.assertEqual(self.request('post','assignments/availability',json=invalid).status_code,400)

    def test_future_day_and_service_date_restrictions_are_independent(self):
        row=self.sunday_row()
        self.assignments.now=lambda:datetime(2026,10,25,3,59,tzinfo=timezone.utc)
        self.assertEqual(self.reserve_at(row,'2026-10-25T18:00:00-04:00').status_code,409)
        self.assignments.now=lambda:datetime(2026,10,25,4,tzinfo=timezone.utc)
        self.assertEqual(self.reserve_at(row,'2026-10-24T18:00:00-04:00').status_code,409)
        self.assertEqual(self.reserve_at(row,'2026-10-25T18:00:00-04:00').status_code,200)

    def test_targeted_slide_requires_valid_offset_service_context(self):
        row=self.sunday_row()
        self.assertEqual(self.reserve(row).status_code,409)
        for invalid in ['2026-10-25','2026-10-25T18:00:00','invalid',1,False,[],{}]:
            response=self.reserve_at(row,invalid)
            self.assertEqual(response.status_code,400,(invalid,response.json))
        with server.connect_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM media_share_assignments').fetchone()[0],0)

    def test_transaction_rechecks_sunday_slot_after_a_concurrent_edit(self):
        row=self.sunday_row('am')
        source=self.assignments.source
        def edit_after_read(ident):
            old=source(ident)
            with server.connect_db() as db:
                db.execute("UPDATE media_share_drops SET target_service_period='pm' WHERE id=?",(ident,))
            return old
        with patch.object(self.assignments,'source',side_effect=edit_after_read):
            rejected=self.reserve_at(row,'2026-10-25T10:30:00-04:00')
        self.assertEqual(rejected.status_code,409,rejected.json)
        with server.connect_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM media_share_assignments').fetchone()[0],0)

    def test_legacy_firestore_period_is_verified_instead_of_client_override(self):
        row=dict(id='legacy-sunday',imageKey='legacy-image')
        values=dict(kind='drop',images=[dict(key=row['imageKey'],chunks=1)],
                    availableStartingDate='2026-10-25',targetServicePeriod='pm')
        response=MagicMock();response.status_code=200;response.__enter__.return_value=response
        response.json.return_value={'fields':{key:field(value) for key,value in values.items()}}
        self.assignments.now=lambda:datetime(2026,10,25,14,tzinfo=timezone.utc)
        with patch('media_share_assignments.requests.get',return_value=response):
            rejected=self.request('post','assignments/reserve',json={**self.claim(row),
                'serviceStart':'2026-10-25T10:30:00-04:00','targetServicePeriod':'am'})
            self.assertEqual(rejected.status_code,409,rejected.json)
            accepted=self.reserve_at(row,'2026-10-25T18:00:00-04:00')
        self.assertEqual(accepted.status_code,200,accepted.json)
        self.assertEqual(accepted.json['assignment']['drop']['targetServicePeriod'],'pm')

    def test_reuse_keeps_independent_receipts_and_title_snapshots_after_restart(self):
        row=self.upload().json
        self.reserve(row)
        first={**self.claim(row),'destination':'youtube','destinationId':'video1'}
        self.assertEqual(self.request('post','assignments/used',json=first).status_code,200)
        original=self.get_claims().json['assignments'][0]
        payload={**self.claim(row,'youtube:video2'),'serviceStart':'2026-10-21T19:30:00-04:00','selectionMode':'manual'}
        reused=self.request('post','assignments/reserve',json=payload)
        self.assertEqual(reused.status_code,200,reused.json)
        self.assertEqual(reused.json['assignment']['receipts'],{})
        self.assertEqual(self.request('post','assignments/used',json={**payload,'destination':'planning_center','destinationId':'episode2'}).status_code,200)
        server.media_share.initialize()
        self.assertEqual(self.get_claims().json['assignments'][0],original)
        second=self.get_claims('youtube:video2').json['assignments'][0]
        self.assertEqual(second['receipts'],{'planning_center:image:episode2':row['imageKey']})
        with server.connect_db() as db:
            db.execute("UPDATE media_share_drops SET title_status='complete',title='Late title',title_revision=1 WHERE id=?",(row['id'],))
        self.assertEqual(self.get_claims().json['assignments'][0]['drop']['autoTitle'],'Late title')
        self.assertEqual(self.get_claims('youtube:video2').json['assignments'][0]['drop']['autoTitle'],'Late title')
        self.assertEqual(self.reserve(row,'youtube:video3').status_code,409)

    def test_undated_floor_survives_deletion_and_restart_but_manual_and_exact_day_work(self):
        older=self.upload('older-slide').json
        newest=self.upload('newer-slide').json
        with server.connect_db() as db:
            db.execute('UPDATE media_share_drops SET created=? WHERE id=?',(100,older['id']))
            db.execute('UPDATE media_share_drops SET created=? WHERE id=?',(200,newest['id']))
        self.assertEqual(self.reserve(newest).status_code,200)
        with server.connect_db() as db:
            db.execute('DELETE FROM media_share_drops WHERE id=?',(newest['id'],))
        server.media_share.initialize()
        self.assertEqual(self.reserve(older,'youtube:video2').status_code,409)
        manual={**self.claim(older,'youtube:video2'),'serviceStart':'2026-10-21T19:30:00-04:00','selectionMode':'manual'}
        self.assertEqual(self.request('post','assignments/reserve',json=manual).status_code,200)
        # Dated slides are separate explicit service targets, not generic backlog.
        target=self.upload('exact-day-slide').json
        with server.connect_db() as db:
            db.execute("UPDATE media_share_drops SET created=50,available_starting_date='2026-10-21' WHERE id=?",(target['id'],))
        self.assertEqual(self.reserve_at(target,'2026-10-21T19:30:00-04:00','youtube:video3').status_code,200)

    def test_parallel_manual_reuse_does_not_transfer_or_duplicate_claims(self):
        row=self.upload().json
        self.reserve(row)
        def worker(service):
            with server.app.test_client() as client:
                return client.post('/api/media-share/assignments/reserve',json={**self.claim(row,service),
                    'serviceStart':'2026-10-21T19:30:00-04:00','selectionMode':'manual'},headers={'Authorization':'Bearer test'}).status_code
        with patch.object(server.media_share.identity,'verify',return_value={'sub':'manager'}), ThreadPoolExecutor(3) as pool:
            self.assertEqual(list(pool.map(worker,['youtube:video2','youtube:video3','youtube:video2'])),[200,200,200])
        with server.connect_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM media_share_assignments').fetchone()[0],3)
        self.assertEqual(self.get_claims().json['assignments'][0]['serviceId'],'youtube:video1')

    def test_legacy_single_owner_database_migrates_without_losing_receipts(self):
        row=self.upload().json
        self.reserve(row)
        self.request('post','assignments/used',json={**self.claim(row),'destination':'youtube','destinationId':'video1'})
        before=self.get_claims().json['assignments'][0]
        with server.connect_db() as db:
            sql=db.execute("SELECT sql FROM sqlite_master WHERE name='media_share_assignments'").fetchone()[0]
            sql=sql.replace('media_share_assignments','legacy',1).replace('image_key TEXT NOT NULL','image_key TEXT PRIMARY KEY').replace(', PRIMARY KEY(image_key,service_id)','')
            db.execute(sql)
            db.execute('INSERT INTO legacy SELECT * FROM media_share_assignments')
            db.execute('DROP TABLE media_share_assignments')
            db.execute('ALTER TABLE legacy RENAME TO media_share_assignments')
            db.execute('DROP TABLE media_share_auto_floor')
        server.media_share.initialize()
        server.media_share.initialize()
        self.assertEqual(self.get_claims().json['assignments'][0],before)
        response=self.request('post','assignments/reserve',json={**self.claim(row,'youtube:video2'),
            'serviceStart':'2026-10-21T19:30:00-04:00','selectionMode':'manual'})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.get_claims().json['assignments'][0],before)


if __name__=='__main__':unittest.main()
