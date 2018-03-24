# coding=utf-8

from __future__ import absolute_import
import sarge
from twilio.rest import Client as TwilioRestClient
import logging
import octoprint.plugin
import os
import phonenumbers
import datetime
import octoprint.util
import re
import uuid


class UploaderBase(object):
    def __init__(self, logger):
        self.have_imports = False
        self._logger = logger # logging.getLogger(__name__)

    # taking a settings dict lets us create a factory method. Otherwise
    # our init classes have to be different.
    @staticmethod
    def factory(provider=None, settings={}):
        _logger = logging.getLogger(__name__)
        _logger.warn("logger name:{}".format(__name__))
        _logger.warn("starting factory with provider: {}".format(provider))

        if provider == 'cloudinary':
            _logger.warn("hello cloudinary")
            return CloudinaryUploader(_logger, settings)
        if provider == 'uploads.im':
            _logger.warn("hello uploads.im")
            return UploadsImUploader(_logger, settings)
        if provider == 'aws_s3':
            _logger.warn("hello s3")
            return S3Uploader(_logger, settings)
        _logger.warn("hello base")
        return UploaderBase(_logger)  # basically a no-op.

    def do_upload(self, file_path, suggested_filename=None):
        pass


class UploadsImUploader(UploaderBase):
    def __init__(self, logger, _):
        super(UploadsImUploader, self).__init__(logger)

    def do_upload(self, file_path, suggested_filename=None):
        file_dict = {"upload": open(file_path, "rb")}
        try:
            import requests
            response = requests.post('http://uploads.im/api?', files=file_dict)
        except Exception as e:
            self._logger.exception(
                "Error Uploading file to uploads.im: {message}".format(
                    message=str(e)))
        else:
            if response.status_code == requests.codes.ok:
                url = response.json().get('data', {}).get('img_url', None)
                self._logger.info("Snapshot uploaded to {}".format(url))
                return url
        return None


class CloudinaryUploader(UploaderBase):
    def __init__(self, logger, _):
        super(CloudinaryUploader, self).__init__(logger)

    def do_upload(self, file_path, suggested_filename=None):
        try:
            from cloudinary import uploader
            response = uploader.unsigned_upload(file_path, "snapshot", cloud_name="octoprint-twilio")
        except Exception as e:
            self._logger.exception("Error Uploading image to cloudinary: {message}".format(message=str(e)))
            return None
        else:
            url = response.get('url', None)
            self._logger.info("Snapshot uploaded, url: {}".format(url))
            return url
        return None


class S3Uploader(UploaderBase):
    def __init__(self, logger, settings):
        super(S3Uploader, self).__init__(logger)
        self.s3_bucket = settings.get(['s3_bucket'])
        self.s3_key_prefix = settings.get(['s3_key_prefix'])
        if not self.s3_key_prefix:
            self.s3_key_prefix = ''
        self.s3_url = settings.get(['s3_url'])
        if not self.s3_url:
            self.s3_url = 'http://{}.s3.amazonaws.com/{}'.format(self.s3_bucket)
        self._logger.warn("fp: {}, s3b: {}, kp: {}".format("n/a", self.s3_bucket, self.s3_key_prefix))
        self._logger.warn("s3up init done")

        try:
            import boto3
            self.s3_client = boto3.client('s3')
            self.have_imports = True
        except Exception as e:
            self._logger.exception(
                "Couldn't import boto3 and start client: {message}".format(
                    message=str(e)))

    def do_upload(self, file_path, suggested_filename=None):
        # we can use the suggested filename to make a sane key, but
        # create a random key otherwise.
        if not suggested_filename:
            suggested_filename = '{}.jpg'.format(uuid.uuid4())

        keypath = '{}{}'.format(self.s3_key_prefix, suggested_filename)
        try:
            self._logger.warn("fp: {}, s3b: {}, kp: {}".format(file_path, self.s3_bucket, keypath))
            self.s3_client.upload_file(file_path, self.s3_bucket, keypath,
                ExtraArgs={
                    'ACL': 'public-read',
                    'CacheControl': 'max-age=300',
                    'ContentType': 'image/jpeg'
                })

            # this can cause a redirect, which Twilio doesn't like.
            #url = self.s3_client.generate_presigned_url('get_object',
            #    Params={'Bucket': self.s3_bucket, 'Key': keypath},
            #    ExpiresIn=3600
            #)
            url = '{}/{}'.format(self.s3_url, keypath)
            self._logger.info("using boto3 url: {}".format(url))
            return url

        except Exception as e:
            self._logger.exception("Error Uploading file to aws s3: {message}".format(
                message=str(e)))
        return None


class SMSNotifierPlugin(octoprint.plugin.EventHandlerPlugin,
                        octoprint.plugin.SettingsPlugin,
                        octoprint.plugin.TemplatePlugin):

    # SettingsPlugin

    def get_settings_defaults(self):
        # matching password must be registered in system keyring
        # to support customizable mail server, may need port too
        return dict(
            enabled=False,
            send_image=False,
            recipient_number="",
            from_number="",
            account_sid="",
            auth_token="",
            printer_name="",
            #upload_provider="cloudinary",
            upload_provider="aws_s3",
            s3_bucket="",
            s3_url="",
            s3_key_prefix="",
            message_format=dict(
                body="{printer_name} job complete: {filename} done printing after {elapsed_time}"
            )
        )

    def get_settings_version(self):
        return 1

    # TemplatePlugin

    def get_template_configs(self):
        return [
            dict(type="settings", name="SMS Notifier", custom_bindings=False)
        ]

    # EventPlugin

    def on_event(self, event, payload):
        if event != "PrintDone":
            return

        if not self._settings.get(['enabled']):
            return
        uploader = UploaderBase.factory(
            provider=self._settings.get(['upload_provider']),
            settings=self._settings)

        snapshot_path = None  # we'll fill it in later
        if self._settings.get(['send_image']):
            snapshot_url = self._settings.global_get(["webcam", "snapshot"])
            if snapshot_url:
                self._logger.info("Taking Snapshot.... Say Cheese!")
                try:
                    import urllib
                    snapshot_path, headers = urllib.urlretrieve(snapshot_url)
                except Exception as e:
                    self._logger.exception(
                        "Exception while fetching snapshot from webcam, sending only a note: {message}".format(
                            message=str(e)))
                else:
                    # ffmpeg can't guess file type it seems
                    os.rename(snapshot_path, snapshot_path + ".jpg")
                    snapshot_path += ".jpg"
                    # flip or rotate as needed
                    self._logger.info("Processing %s before uploading." % snapshot_path)
                    self._process_snapshot(snapshot_path)

        self._logger.info("calling generic uploader, uploader: {}, file: {}".format(uploader, snapshot_path))
        snapshot_file = '{}_{}.jpg'.format(payload['name'], octoprint.util.get_formatted_datetime(datetime.datetime.now()))
        image_url = uploader.do_upload(snapshot_path, self.scrub_filename(snapshot_file))
        self._logger.info("done calling generic uploader, url: {}".format(image_url))

        # we have the image, or not. either way, send the sms.
        self._send_txt_with_image(payload, image_url)

    def _send_txt_with_image(self, payload, img_path=None):
        sent = False
        try:
            # try to safely send a message- in other words, fall back as needed. Nested 'if' cases
            # are easier to read, hopefully. Note returning early on success.
            if img_path:
                sent = self._send_txt(payload, img_path)
                if sent:
                    self._logger.warn("successfully sent text+image notification")
                    return True
                else:
                    self._logger.warn("could not send text+image notification, will try without image.")

            self._logger.warn("sending text-only text notification.")
            sent = self._send_txt(payload)
            if sent:
                self._logger.warn("successfully sent text-only notification")
                return True
            else:
                self._logger.warn("could not sent text-only notification for unknown reasons.")
                return False
        except Exception as e:
            self._logger.exception("notification error: %s" % (str(e)))
        # fallthrough
        return sent

    def _send_txt(self, payload, snapshot=False):

        filename = os.path.basename(payload["file"])

        elapsed_time = octoprint.util.get_formatted_timedelta(datetime.timedelta(seconds=payload["time"]))

        fromnumber = phonenumbers.format_number(phonenumbers.parse(
            self._settings.get(['from_number']), 'US'), phonenumbers.PhoneNumberFormat.E164)

        for number in self._settings.get(['recipient_number']).split(','):
            tonumber = phonenumbers.format_number(phonenumbers.parse(number, 'US'), phonenumbers.PhoneNumberFormat.E164)
        tags = {
            'filename': filename,
            'elapsed_time': elapsed_time,
            'printer_name': self._settings.get(["printer_name"])
        }
        message = self._settings.get(["message_format", "body"]).format(**tags)

        client = TwilioRestClient(self._settings.get(['account_sid']), self._settings.get(['auth_token']))
        if snapshot:
            try:
                client.messages.create(to=tonumber, from_=fromnumber, body=message, media_url=snapshot)
            except Exception as e:
                # report problem sending sms
                self._logger.exception("SMS notification error: %s" % (str(e)))
                return False
            else:
                # report notification was sent
                self._logger.info("Print notification sent to %s" % (self._settings.get(['recipient_number'])))
                return True

        try:
            client.messages.create(to=tonumber, from_=fromnumber, body=message)
        except Exception as e:
            # report problem sending sms
            self._logger.exception("SMS notification error: %s" % (str(e)))
        else:
            # report notification was sent
            self._logger.info("Print notification sent to %s" % (self._settings.get(['recipient_number'])))
            return True

        return False

    def _process_snapshot(self, snapshot_path, pixfmt="yuv420p"):
        hflip = self._settings.global_get_boolean(["webcam", "flipH"])
        vflip = self._settings.global_get_boolean(["webcam", "flipV"])
        rotate = self._settings.global_get_boolean(["webcam", "rotate90"])
        ffmpeg = self._settings.global_get(["webcam", "ffmpeg"])

        if not ffmpeg or not os.access(ffmpeg, os.X_OK) or (not vflip and not hflip and not rotate):
            return

        ffmpeg_command = [ffmpeg, "-y", "-i", snapshot_path]

        rotate_params = ["format={}".format(pixfmt)]  # workaround for foosel/OctoPrint#1317
        if rotate:
            rotate_params.append("transpose=2")  # 90 degrees counter clockwise
        if hflip:
            rotate_params.append("hflip")       # horizontal flip
        if vflip:
            rotate_params.append("vflip")       # vertical flip

        ffmpeg_command += ["-vf", sarge.shell_quote(",".join(rotate_params)), snapshot_path]
        self._logger.info("Running: {}".format(" ".join(ffmpeg_command)))

        p = sarge.run(ffmpeg_command, stdout=sarge.Capture(), stderr=sarge.Capture())
        if p.returncode == 0:
            self._logger.info("Rotated/flipped image with ffmpeg")
        else:
            self._logger.warn("Failed to rotate/flip image with ffmpeg, "
                              "got return code {}: {}, {}".format(p.returncode,
                                                                  p.stdout.text,
                                                                  p.stderr.text))

    def scrub_filename(self, filename):
        '''Restrict characters used in a filename'''
        return re.sub(r"[^\w\-\.]", '_', filename)

    def get_update_information(self):
        return dict(
            smsnotifier=dict(
                displayName="SMSNotifier Plugin",
                displayVersion=self._plugin_version,

                # version check: github repository
                type="github_release",
                user="taxilian",
                repo="OctoPrint-Twilio",
                current=self._plugin_version,

                # update method: pip
                pip="https://github.com/taxilian/OctoPrint-Twilio/archive/{target_version}.zip",
                dependency_links=False
            )
        )


__plugin_name__ = "SMS Notifier (with Twilio)"


def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = SMSNotifierPlugin()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information
    }
