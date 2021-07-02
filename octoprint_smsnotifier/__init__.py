# coding=utf-8
from __future__ import absolute_import, division, print_function, unicode_literals

import sys

if sys.version_info[0] >= 3:
    from urllib.request import urlretrieve
else:
    from urllib import urlretrieve

import os
import datetime
import octoprint.plugin
import octoprint.util
import phonenumbers
import sarge
from twilio.rest import Client as TwilioRestClient
from twilio.base import values


__plugin_pythoncompat__ = ">=2.7,<4"


class SMSNotifierPlugin(
    octoprint.plugin.EventHandlerPlugin,
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.TemplatePlugin,
    octoprint.plugin.AssetPlugin
):
    # Class variables
    NO_SNAPSHOT = values.unset

    # SettingsPlugin

    def get_settings_defaults(self):
        return dict(
            enabled=False,
            send_image=False,
            recipient_number="",
            from_number="",
            account_sid="",
            auth_token="",
            printer_name="",
            message_format=dict(
                body="{printer_name} job complete: {filename} done printing after {elapsed_time}"
            ),
            events=[]
        )

    def get_settings_version(self):
        return 2

    # Migrate from one version of the plugin to another
    def on_settings_migrate(self, target, current=None):

        # If configuration not found then do nothing
        if not current:
            self._logger.warning('No current configuration found to migrate.')
            return

        self._logger.info('Plugin Settings have changed from {} to {}.'.format(current, target))

        if current < 2:
            self._logger.info('Migrating to new Events system.')

            prev_body = self._settings.get(["message_format", "body"])

            if not prev_body:
                self._logger.warning('No previous message_format to migrate.')
                return

            new_body = prev_body.replace('{filename}', '{name}')
            new_body = new_body.replace('{elapsed_time}', '{time}')
            new_body = new_body.replace('{printer_name}', self._settings.get(['printer_name']))

            default_event = dict(
                name='PrintDone',
                message=new_body,
                take_pic=self._settings.get(['send_image'])
            )

            self._settings.set(['events'], [default_event])

            self._logger.info('Finished Migrating to new Events system.')
            self._logger.debug('Migrated {} to {}.'.format(prev_body, new_body))

    # TemplatePlugin

    def get_template_configs(self):
        return [
            dict(type="settings", name="SMS Notifier", custom_bindings=True)
        ]

    def get_template_vars(self):
        return dict(
            name=self._plugin_name,
            version=self._plugin_version
        )

    # Asset Plug

    def get_assets(self):
        return dict(
            js=['js/smsnotifier.js'],
            css=['css/smsnotifier.css']
        )

    # EventPlugin

    def on_event(self, event, payload):

        if not self._settings.get(['enabled']):
            return

        event_config = None

        for config in self._settings.get(['events']):
            if event == config['name']:
                event_config = config
                break

        if not event_config:
            self._logger.warning('No events configured for {}'.format(event))
            return

        snapshot = self.NO_SNAPSHOT

        if event_config['take_pic']:
            snapshot = self._take_snapshot()

        return self._send_txt(event_config, payload, snapshot)

    def _take_snapshot(self):
        webcam_url = self._settings.global_get(["webcam", "snapshot"])

        if not webcam_url:
            self._logger.warn("Could not find settings for snapshot URL. Is it enabled?")
            return self.NO_SNAPSHOT

        self._logger.info("Taking picture.... Say Cheese!")

        try:
            snapshot_path, headers = urlretrieve(webcam_url)
        except Exception as e:
            self._logger.exception("Exception while fetching snapshot from webcam, sending only a note: {message}".format(
                message=str(e)))
            return self.NO_SNAPSHOT

        # ffmpeg can't guess file type it seems
        os.rename(snapshot_path, snapshot_path + ".jpg")
        snapshot_path += ".jpg"
        # flip or rotate as needed
        self._logger.info("Processing %s before uploading." % snapshot_path)
        self._process_snapshot(snapshot_path)

        try:
            from cloudinary import uploader
            response = uploader.unsigned_upload(snapshot_path, "snapshot", cloud_name="octoprint-twilio")
        except Exception as e:
            self._logger.exception("Error Uploading image to the cloud: {message}".format(message=str(e)))
            return self.NO_SNAPSHOT

        if "url" not in response:
            self._logger.error("Cloud returned {}".format(response["error"]["message"]))
            return self.NO_SNAPSHOT

        self._logger.info("Snapshot uploaded to {}".format(response["url"]))
        return response["url"]

    def _send_txt(self, event_config, payload, media_url=values.unset):

        if 'time' in payload:
            payload['time'] = octoprint.util.get_formatted_timedelta(datetime.timedelta(seconds=payload["time"]))

        fromnumber = phonenumbers.format_number(phonenumbers.parse(self._settings.get(['from_number']), 'US'), phonenumbers.PhoneNumberFormat.E164)

        message = event_config['message'].format(**payload)

        client = TwilioRestClient(self._settings.get(['account_sid']), self._settings.get(['auth_token']))

        for number in self._settings.get(['recipient_number']).split(','):
            tonumber = phonenumbers.format_number(phonenumbers.parse(number, 'US'), phonenumbers.PhoneNumberFormat.E164)

            try:
                client.messages.create(to=tonumber, from_=fromnumber, body=message, media_url=media_url)
            except Exception as e:
                # report problem sending sms
                self._logger.error("SMS notification error: %s" % (str(e)))
                continue
            else:
                # report notification was sent
                self._logger.info("Print notification sent to %s" % (tonumber))

        # all messages were attempted to be sent
        return True

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
