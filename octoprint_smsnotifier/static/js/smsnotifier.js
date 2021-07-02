$(function () {
  function smsnotifierViewModel(parameters) {
    var self = this;

    self.settingsViewModel = parameters[0]; // requested as first dependency below

    // Plugin Settings
    self.enabled = ko.observable(false)
    self.recipient_number = ko.observable("")
    self.account_sid = ko.observable("")
    self.auth_token = ko.observable("")
    self.from_number = ko.observable("")
    self.events = ko.observableArray([])


    self.onBeforeBinding = function() {
      self.enabled(self.settingsViewModel.settings.plugins.smsnotifier.enabled())
      self.recipient_number(self.settingsViewModel.settings.plugins.smsnotifier.recipient_number())
      self.account_sid(self.settingsViewModel.settings.plugins.smsnotifier.account_sid())
      self.auth_token(self.settingsViewModel.settings.plugins.smsnotifier.auth_token())
      self.from_number(self.settingsViewModel.settings.plugins.smsnotifier.from_number())
      self.events(self.settingsViewModel.settings.plugins.smsnotifier.events())

      if(!self.events().length){
        self.events.push({
          name: "PrintDone",
          take_pic: false,
          message: "OctoPrint: File {name} done printing after {time}"
        })
      }
		};

    self.onSettingsBeforeSave = function() {
      self.settingsViewModel.settings.plugins.smsnotifier.enabled(self.enabled())
      self.settingsViewModel.settings.plugins.smsnotifier.recipient_number(self.recipient_number())
      self.settingsViewModel.settings.plugins.smsnotifier.account_sid(self.account_sid())
      self.settingsViewModel.settings.plugins.smsnotifier.auth_token(self.auth_token())
			self.settingsViewModel.settings.plugins.smsnotifier.from_number(self.from_number())
			self.settingsViewModel.settings.plugins.smsnotifier.events(self.events())
		};

    self.addEvent = function () {
      self.events.push({
        name: "",
        take_pic: false,
        message: ""
      });
    };

    self.removeEvent = function (event) {
      self.events.remove(event);
    };
  };

  OCTOPRINT_VIEWMODELS.push({
    construct: smsnotifierViewModel,
    dependencies: ["settingsViewModel"],
    elements: ["#smsnotifierViewModel"]
  });
})