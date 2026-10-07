"""Account-owned, bounded raster avatar processing. Originals/metadata are discarded."""
from __future__ import annotations
import hashlib
import io
import os
import secrets
import warnings
from flask import jsonify, request, send_file
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD = 6*1024*1024
MAX_PIXELS = 32_000_000
AVATAR_SIZE = 256


def compress_avatar(raw: bytes) -> bytes:
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as image:
            if image.width*image.height>MAX_PIXELS or min(image.size)<1:
                raise ValueError('Image dimensions are too large.')
            image.seek(0)
            image = ImageOps.exif_transpose(image)
            image = ImageOps.fit(image, (AVATAR_SIZE,AVATAR_SIZE), Image.Resampling.LANCZOS, centering=(.5,.5)).convert('RGB')
            # Creating a fresh raster strips EXIF/GPS, ICC and uploaded metadata.
            clean=Image.new('RGB',image.size);clean.paste(image)
            output=io.BytesIO();clean.save(output,format='WEBP',quality=82,method=4)
            return output.getvalue()


class Profile:
    def __init__(self,venture):
        self.v=venture
        self.register_routes()

    def path(self,uid):return self.v.user_root(uid)/'profile'/'avatar.webp'

    def snapshot(self,uid):
        with self.v.mirror_lock:
            path=self.path(uid)
            present=path.is_file()
            return dict(hasAvatar=present,avatarVersion=hashlib.sha256(path.read_bytes()).hexdigest()[:20] if present else None)

    def register_routes(self):
        @self.v.app.route('/api/venture/profile',methods=['GET','PUT','DELETE'])
        def venture_profile():
            uid=self.v.account();path=self.path(uid)
            if request.method=='PUT':
                request.max_content_length=MAX_UPLOAD+65536
                if request.form or set(request.files)!={'avatar'} or len(request.files.getlist('avatar'))!=1:
                    raise self.v.Error('Choose one profile image.')
                raw=request.files['avatar'].stream.read(MAX_UPLOAD+1)
                if not raw or len(raw)>MAX_UPLOAD:raise self.v.Error('The profile image is too large.',413)
                try:data=compress_avatar(raw)
                except (ValueError,OSError,UnidentifiedImageError,Image.DecompressionBombError,Image.DecompressionBombWarning):
                    raise self.v.Error('Choose a valid JPEG, PNG or WebP photo. SVG files are not profile photos.',415) from None
                with self.v.mirror_lock:
                    path.parent.mkdir(parents=True,exist_ok=True)
                    temporary=path.with_name('.avatar-'+secrets.token_hex(8)+'.tmp')
                    try:
                        with temporary.open('wb') as stream:
                            stream.write(data);stream.flush();os.fsync(stream.fileno())
                        os.replace(temporary,path)
                    finally:temporary.unlink(missing_ok=True)
            elif request.method=='DELETE':
                with self.v.mirror_lock:path.unlink(missing_ok=True)
            return jsonify(self.snapshot(uid))

        @self.v.app.get('/api/venture/profile/avatar')
        def venture_avatar():
            path=self.path(self.v.account())
            if not path.is_file():raise self.v.Error('No custom profile image is saved.',404)
            return send_file(path,mimetype='image/webp',max_age=0)

        @self.v.app.get('/api/venture/storage')
        def venture_storage():
            uid=self.v.account();root=self.v.user_root(uid)
            return jsonify(userDirectory=str(root),conversationsDirectory=str(root/'conversations'),
                           avatarPath=str(self.path(uid)),identityPath=str(root/'user.json'),
                           databasePath=str(self.v.app.config['DATABASE']),
                           dictationAudio='Temporary only; removed after transcription. Text is saved in a conversation only after Send.')
