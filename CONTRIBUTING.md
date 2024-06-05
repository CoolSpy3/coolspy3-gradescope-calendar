# Contributing
## How to contribute
This project is open source which means that the code is available on GitHub for all to see and update. If you want to contribute to the project, you can do so by forking the repository and submitting a pull request. Alternatively, if you have an idea for something that would be cool, but don't know how to implement it, you can open an issue on GitHub and I'll see what I can do.

## Code of Conduct
I'll try to keep this brief. Basically, be nice to people and don't do anything illegal. Content which violates this policy will be deleted and violators may be banned from contributing to the project. For a more detailed explanation of allowed behaviors, take a look at the [Contributor Covenant](https://www.contributor-covenant.org/version/2/1/code_of_conduct/).

## Testing the site locally
I will open this by saying please do not try to test changes against the actual site. This includes pentesting, profiling, bug hunting, and any other form of testing you can think of. If you want to test the site, you can do so locally. The site uses firebase, so testing it locally should be as easy as starting the [Firebase Emulator Suite](https://firebase.google.com/docs/emulator-suite/connect_and_prototype). However, there are a few areas of code which will not work because they rely on secrets which are only available in production. I've listed some workarounds below:
* Google Calendar access
  * Frontend
    * The frontend uses the Google Calendar API to list the user's calendars can calendar colors. Firebase's authentication emulator does not (as far as I can tell) allow you to link a live Google account to the simulation. In order to work around this, you can go to the file `public/js/dashboard.js` and change the line `gapi.client.setToken({access_token: user.credentials.accessToken});` to `gapi.client.setToken({access_token: "<YOUR TOKEN HERE>"});` You can obtain a token by downloading the [Python script version of this project](https://github.com/CoolSpy3/gradescope-calendar), configuring a google OAuth ID and Client Secret (as explained in that project's README), starting a python shell, and running the following code:
    ```python
    import utils
    utils.login_with_google()
    ```
    The code will then place the access token into a json file in the current directory.
  * Backend
    * The backend uses the Google Calendar API to add events to the user's calendar. Because there is no way to link a Google account through the emulator, there is no way to access it through the backend. In order to work around this, you can go to the file `functions/python/main.py`. Set the `debug` flag at the top of the file to `True` and fill out the `debug_config` with your credentials. This will cause the backend to use the given credentials to sign in to Google
