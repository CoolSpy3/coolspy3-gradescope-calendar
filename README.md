# coolspy3-gradescope-calendar
This project is the web continuation of [CoolSpy3/gradescope-calendar](https://gihtub.com/CoolSpy3/gradescope-calendar). It is a web app that allows you to import your Gradescope calendar into your Google Calendar.

The site is currently hosted at [https://coolspy3-gradescope-calendar.web.app/](https://coolspy3-gradescope-calendar.web.app/).

For information on how to use the site and how the implementation works on a high level, please read [this project's wiki](https://github.com/CoolSpy3/coolspy3-gradescope-calendar/wiki/)

## Project Layout
Here is a basic overview of the project layout. I've tried to list the main directories and files, but I've left out some of the less important ones.
```
- ./
  |- functions - Firebase Cloud Functions
  |  |- python - Python functions (Fetching Gradescope data and pushing to Google Calendar)
  |  |  |- main.py          - Main functions and top-level code
  |  |  |- requirements.txt - Python dependencies
  |  |  \- utils.py         - Helper functions
  |  |
  |  \- typescript - TypeScript functions (User deletion)
  |     \- src
  |        \- index.ts - Typescript function code
  |
  |- public - Static website code (HTML, CSS, JS, Images, etc.)
  |  |- css
  |  \- js
  |
  |- database_structure.json - Firebase Realtime Database structure
  |                            (This is for reference purposes only. It is not used by firebase.)
  \- firebase.json           - Firebase configuration

```

## Database Layout
The database is divided into multiple sections for each type of information. Each of these is further divided into separate sections for each user. For more information see the [How is Data Stored?](about:blank) page of our documentation and the [database_structure.json](database_structure.json) file in the root directory of this repo.

## Contributing
If you would like to contribute to this project, please read [CONTRIBUTING.md](CONTRIBUTING.md).
