"""Offline availability-date persistence, migration, and Firestore contract."""
import io
from unittest.mock import patch
import unittest

from PIL import Image
import server
from media_share import available_starting_date
import test_media_share


class MediaAvailabilityTests(unittest.TestCase):
    setUp = test_media_share.MediaShareTests.setUp
    tearDown = test_media_share.MediaShareTests.tearDown
    request = test_media_share.MediaShareTests.request

    def upload(self, rid='availability-test', available=None, prior=None, period=None):
        image=io.BytesIO()
        Image.new('RGB',(160,90),'navy').save(image,'PNG')
        data={'requestId':rid,'image':(io.BytesIO(image.getvalue()),'slide.png')}
        if available is not None:data['availableStartingDate']=available
        if period is not None:data['targetServicePeriod']=period
        if prior:data['revision']=str(prior['revision'])
        return self.request('post','drops'+('/'+prior['id'] if prior else ''),data=data)

    def test_optional_date_persists_across_restart_and_publication(self):
        response=self.upload(available='2026-11-03')
        self.assertEqual(response.status_code,202,response.json)
        row=response.json
        self.assertEqual(row['availableStartingDate'],'2026-11-03')
        server.media_share.initialize()
        restored=self.request('get','drops/'+row['id']).json
        self.assertEqual(restored['availableStartingDate'],'2026-11-03')
        with patch.object(server.media_share.publisher,'write') as write:
            server.media_share.publisher.publish(server.media_share.row(row['id']))
        self.assertEqual(write.call_args.args[1]['availableStartingDate'],'2026-11-03')

    def test_clear_and_replacement_preserve_explicit_semantics(self):
        original=self.upload(available='2026-11-03').json
        replaced=self.upload('replace-available',prior=original).json
        self.assertEqual(replaced['availableStartingDate'],'2026-11-03')
        cleared=self.request('patch','drops/'+replaced['id'],json={
            'revision':replaced['revision'],'availableStartingDate':''})
        self.assertEqual(cleared.status_code,200,cleared.json)
        self.assertEqual(cleared.json['availableStartingDate'],'')
        unrestricted=self.upload('no-date-selected').json
        self.assertEqual(unrestricted['availableStartingDate'],'')

    def test_invalid_dates_fail_without_committing_other_edits(self):
        row=self.upload().json
        for invalid in ['2026-02-30','2026-1-02','2026-11-03T00:00:00Z',1,False,[],{}]:
            response=self.request('patch','drops/'+row['id'],json={
                'revision':row['revision'],'availableStartingDate':invalid,'text':'must roll back'})
            self.assertEqual(response.status_code,400,(invalid,response.json))
            current=self.request('get','drops/'+row['id']).json
            self.assertEqual(current['text'],'')
            self.assertEqual(current['revision'],row['revision'])
        self.assertEqual(self.upload('bad-date-upload',available='2026-02-30').status_code,400)

    def test_existing_schema_migration_defaults_to_unrestricted(self):
        row=self.upload().json
        with server.connect_db() as db:
            db.execute('ALTER TABLE media_share_drops DROP COLUMN available_starting_date')
        server.media_share.initialize()
        restored=self.request('get','drops/'+row['id']).json
        self.assertEqual(restored['availableStartingDate'],'')
        self.assertEqual(available_starting_date(None),'')
        self.assertEqual(available_starting_date('2028-02-29'),'2028-02-29')

    def test_sunday_period_survives_receipt_restart_replacement_and_publication(self):
        row=self.upload(available='2026-10-25',period='pm').json
        self.assertEqual(row['targetServicePeriod'],'pm')
        # A retry cannot silently turn the original upload into another service.
        retried=self.upload(available='2026-11-01',period='am').json
        self.assertEqual(retried['id'],row['id'])
        self.assertEqual(retried['availableStartingDate'],'2026-10-25')
        self.assertEqual(retried['targetServicePeriod'],'pm')
        server.media_share.initialize()
        recovered=self.request('get','requests/availability-test').json
        self.assertEqual(recovered['targetServicePeriod'],'pm')
        replaced=self.upload('sunday-replacement',prior=recovered).json
        self.assertEqual(replaced['targetServicePeriod'],'pm')
        with patch.object(server.media_share.publisher,'write') as write:
            server.media_share.publisher.publish(server.media_share.row(row['id']))
        metadata=write.call_args.args[1]
        self.assertEqual(metadata['availableStartingDate'],'2026-10-25')
        self.assertEqual(metadata['targetServicePeriod'],'pm')

    def test_period_is_validated_atomically_and_non_sunday_date_clears_hidden_choice(self):
        row=self.upload(available='2026-10-25',period='am').json
        for value in ['AM','both',False,1,[],{}]:
            rejected=self.request('patch','drops/'+row['id'],json={
                'revision':row['revision'],'targetServicePeriod':value,'text':'must roll back'})
            self.assertEqual(rejected.status_code,400,(value,rejected.json))
            current=self.request('get','drops/'+row['id']).json
            self.assertEqual(current['text'],'')
            self.assertEqual(current['targetServicePeriod'],'am')
            self.assertEqual(current['revision'],row['revision'])
        for invalid_date in ['', '2026-10-26']:
            rejected=self.request('patch','drops/'+row['id'],json={
                'revision':row['revision'],'availableStartingDate':invalid_date,'targetServicePeriod':'pm'})
            self.assertEqual(rejected.status_code,400,rejected.json)
        changed=self.request('patch','drops/'+row['id'],json={
            'revision':row['revision'],'availableStartingDate':'2026-10-26'})
        self.assertEqual(changed.status_code,200,changed.json)
        self.assertEqual(changed.json['targetServicePeriod'],'')
        self.assertEqual(self.upload('weekday-with-period',available='2026-10-26',period='am').status_code,400)
        self.assertEqual(self.upload('no-date-with-period',period='pm').status_code,400)

    def test_period_schema_migration_preserves_legacy_date(self):
        row=self.upload(available='2026-10-25').json
        with server.connect_db() as db:
            db.execute('ALTER TABLE media_share_drops DROP COLUMN target_service_period')
        server.media_share.initialize()
        restored=self.request('get','drops/'+row['id']).json
        self.assertEqual(restored['targetServicePeriod'],'')
        self.assertEqual(restored['availableStartingDate'],'2026-10-25')


if __name__=='__main__':unittest.main()
