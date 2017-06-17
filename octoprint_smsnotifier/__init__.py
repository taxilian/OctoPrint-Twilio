# coding=utf-8
from __future__ import absolute_import
import os
import octoprint.plugin
from twilio.rest import Client as TwilioRestClient
import phonenumbers

class SMSNotifierPlugin(octoprint.plugin.EventHandlerPlugin,
                          octoprint.plugin.SettingsPlugin,
                          octoprint.plugin.TemplatePlugin):
    
    #~~ SettingsPlugin

    def get_settings_defaults(self):
        # matching password must be registered in system keyring
        # to support customizable mail server, may need port too
        return dict(
            enabled=False,
            recipient_number="",
            from_number="",
            account_sid="",
            auth_token="",
            printer_name="",
            message_format=dict(
                body="{printer_name} job complete: {filename} done printing after {elapsed_time}" 
            )
        )
    
    def get_settings_version(self):
        return 1

    #~~ TemplatePlugin

    def get_template_configs(self):
        return [
            dict(type="settings", name="SMS Notifier", custom_bindings=False)
        ]

    #~~ EventPlugin
    
    def on_event(self, event, payload):
        if event != "PrintDone":
            return
        
        if not self._settings.get(['enabled']):
            return
        
        filename = os.path.basename(payload["file"])
        
        import datetime
        import octoprint.util
        elapsed_time = octoprint.util.get_formatted_timedelta(datetime.timedelta(seconds=payload["time"]))

        
        tags = {
            'filename': filename,
            'elapsed_time': elapsed_time,
            'printer_name': self._settings.get(["printer_name"])
        }
        message = self._settings.get(["message_format", "body"]).format(**tags)
        
        try:
            client = TwilioRestClient(self._settings.get(['account_sid']), self._settings.get(['auth_token']))

            fromnumber = phonenumbers.format_number(phonenumbers.parse(self._settings.get(['from_number']), 'US'), phonenumbers.PhoneNumberFormat.E164)
            for number in self._settings.get(['recipient_number']).split(','):
                tonumber = phonenumbers.format_number(phonenumbers.parse(number, 'US'), phonenumbers.PhoneNumberFormat.E164)
                client.messages.create(to=tonumber,from_=fromnumber,body=message)
        except Exception as e:
            # report problem sending sms
            self._logger.exception("SMS notification error: %s" % (str(e)))
        else:
            # report notification was sent
            self._logger.info("Print notification sent to %s" % (self._settings.get(['recipient_number'])))     

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

