function dashboardErrorHandler(data, msg) {
    alert(msg + " Please contact the developer if this problem continues. Helpful debug information has been dumped to the console.");
    console.error(msg + "\n" )
    console.log("BEGIN HELPFUL DEBUG INFORMATION");
    console.log(data);
    console.log(JSON.stringify(data));
    console.log("END HELPFUL DEBUG INFORMATION");
}

// Code from https://stackoverflow.com/a/30800715
function downloadObjectAsJson(exportObj, exportName){
    var dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(exportObj));
    var downloadAnchorNode = document.createElement('a');
    downloadAnchorNode.setAttribute("href",     dataStr);
    downloadAnchorNode.setAttribute("download", exportName + ".json");
    document.body.appendChild(downloadAnchorNode); // required for firefox
    downloadAnchorNode.click();
    downloadAnchorNode.remove();
}

let uiRendered = false;
let user = null;

firebase.auth().onAuthStateChanged((loggedInUser) => {
    if(uiRendered) return; // onAuthStateChanged will be called again when the user signs in, so set a flag to prevent the code from running twice
    uiRendered = true;
    if (loggedInUser) {
        user = loggedInUser;
        gapi.load('client', () => {
            if(!localStorage.getItem("google_access_token") || !localStorage.getItem("google_access_token_timestamp") || // User is not signed in.
                Date.now() - parseInt(localStorage.getItem("google_access_token_timestamp")) >= 3600000) { // Access token is expired.
                // Redirect to login page.
                localStorage.removeItem("google_access_token");
                localStorage.removeItem("google_access_token_timestamp");
                firebase.auth().signOut();
                window.location.href = "/401";
                return;
            }
            gapi.client.setToken({access_token: localStorage.getItem("google_access_token")});
            gapi.client.load('calendar', 'v3', () => {
                function recursivelyFindCalendars(pageToken = "") {
                    return new Promise((resolve, reject) => {
                        gapi.client.calendar.calendarList.list({minAccessRole:"writer", 'pageToken': pageToken})
                        .then(calendarList => {
                            if(!("nextPageToken" in calendarList) || calendarList.nextPageToken === "" || calendarList.items.length === 0) {
                                resolve(calendarList.result.items);
                            } else {
                                recursivelyFindCalendars(calendarList.nextPageToken).then(moreCalendars => {
                                    resolve(calendarList.result.items.concat(moreCalendars));
                                }).catch(reject);
                            }
                        }).catch(reject);
                    });
                }

                const calendarSelector = document.getElementById("calendar-selector");
                recursivelyFindCalendars().then(calendars => {
                    calendars.filter(calendar => !calendar.deleted).forEach(calendar => {
                        const option = document.createElement("option");
                        option.value = calendar.id;
                        option.innerHTML = calendar.summary;
                        calendarSelector.appendChild(option);
                    });

                    firebase.database().ref("settings/" + user.uid + "/calendar_id").get()
                    .then(calendarId => {
                        if (calendarId.exists()) {
                            const calendarIdVal = calendarId.val();
                            calendarSelector.value = calendarIdVal;
                            // If calendarId.val() is not a valid option, select will set its value to '', however, it will not select an option.
                            // By using this apparent noop, this will select the None option (which has a value of '')
                            calendarSelector.value = calendarSelector.value;
                            // If the value changed, update the database
                            if(calendarSelector.value !== calendarIdVal) {
                                firebase.database().ref("settings/" + user.uid + "/calendar_id").set(calendarSelector.value);
                            }
                        }
                    }).catch(error => {
                        dashboardErrorHandler(error, "An error occurred fetching your calendar selection from our database.");
                    });
                }).catch(error => {
                    dashboardErrorHandler(error, "An error occurred finding your calendars.");
                });

                Promise.all([
                    firebase.database().ref("settings/" + user.uid + "/courses").get().then(courses => courses.val()),
                    gapi.client.calendar.colors.get().then(colors => colors.result.event)
                ]).then(([courses, colors]) => {
                    if (!("1" in colors)) {
                        dashboardErrorHandler(colors, "Calendar color assertion failed! (This should not happen)");
                        return;
                    }

                    function setupColorDropdown(dropdown, initialColor) {
                        for (const [colorId, color] of Object.entries(colors)) {
                            const option = document.createElement("option");
                            option.value = colorId;
                            option.style.backgroundColor = color.background;
                            dropdown.appendChild(option);
                        }

                        dropdown.onchange = () => {
                            if(dropdown.value) {
                                dropdown.style.backgroundColor = colors[dropdown.value].background;
                            } else {
                                dropdown.style.backgroundColor = "#fff";
                            }
                        }

                        dropdown.value = initialColor;
                        dropdown.onchange();
                    }

                    const completedAssignmentColorSelector = document.getElementById("completed-assignment-color-selector");
                    firebase.database().ref("settings/" + user.uid + "/completed_assignment_color").get().then(ref => ref.val())
                        .then(completed_assignment_color => {
                        if(completed_assignment_color && !(completed_assignment_color in colors)) {
                            completed_assignment_color = "1";
                        }
                        if(!completed_assignment_color) {
                            completed_assignment_color = "";
                        }
                        setupColorDropdown(completedAssignmentColorSelector, completed_assignment_color);
                    }).catch(error => {
                        dashboardErrorHandler(error, "An error occurred fetching your completed assignment color.");
                    });

                    if(!courses) {
                        return;
                    }
                    const courseColorSelectors = document.getElementById("course-color-selectors");
                    for (const [courseId, course] of Object.entries(courses)) {
                        if(!("name" in course)) {
                            dashboardErrorHandler(course, "Course name assertion failed! (This should not happen)");
                            return;
                        }

                        if(!("color" in course) || !(course.color in colors)) {
                            course.color = "1";
                        }

                        const courseColorSelectorContainer = document.createElement("div");
                        courseColorSelectorContainer.classList.add("course-color-selector");
                        const courseColorSelectorLabel = document.createElement("label");
                        courseColorSelectorLabel.innerHTML = course.name + ": ";
                        courseColorSelectorLabel.htmlFor = "course-color-selector-" + courseId;
                        courseColorSelectorContainer.appendChild(courseColorSelectorLabel);
                        const courseColorSelector = document.createElement("select");
                        courseColorSelector.id = "course-color-selector-" + courseId;

                        setupColorDropdown(courseColorSelector, course.color);

                        courseColorSelectorContainer.appendChild(courseColorSelector);

                        courseColorSelectors.appendChild(courseColorSelectorContainer);
                    }
                }).catch(error => {
                    dashboardErrorHandler(error, "An error occurred fetching your courses and calendar colors.");
                });
            });
        });

        firebase.database().ref("auth_status/" + user.uid + "/gradescope").get().then(ref => ref.val())
        .then(isAuthValid => {
            if(isAuthValid) {
                const linkGradescopeButton = document.getElementById("link-gradescope-button");
                linkGradescopeButton.classList.remove("blue-button");
                linkGradescopeButton.classList.add("white-button");
            }
        }).catch(error => {
            dashboardErrorHandler(error, "An error occurred fetching your Gradescope authentication status.");
        });

        firebase.database().ref("auth_status/" + user.uid + "/google").get().then(ref => ref.val())
        .then(isAuthValid => {
            if(!isAuthValid) {
                window.location.href = "/link-google";
            }
        }).catch(error => {
            dashboardErrorHandler(error, "An error occurred fetching your Gradescope authentication status.");
        });
    } else {
        window.location.href = "/401";
    }
});

function refreshCourseList() {
    document.getElementById("refresh-course-list-button").disabled = true;
    firebase.functions().httpsCallable("refresh_course_list")()
    .then(result => {
        if (result.data.success) {
            location.reload();
        } else {
            alert("A backend error occurred refreshing your course list!");
        }
    })
    .catch(error => {
        if (error.message === "invalid_gradescope_auth") {
            alert("Error: Invalid Gradescope credentials! Try relinking your Gradescope account.");
        } else {
            dashboardErrorHandler(error, "An error occurred refreshing your course list.");
        }
        document.getElementById("refresh-course-list-button").disabled = false;
    });
}

function refreshEvents() {
    document.getElementById("update-events-button").disabled = true;
    firebase.functions().httpsCallable("refresh_events")()
    .then(result => {
        if (result.data.success) {
            alert("Your events have been successfully updated!");
        } else {
            alert("A backend error occurred refreshing your course list!");
        }
        document.getElementById("update-events-button").disabled = false;
    })
    .catch(error => {
        switch(error.message) {
            case "invalid_gradescope_auth":
                alert("Error: Invalid Gradescope credentials! Try relinking your Gradescope account.");
                break;
            case "invalid_calendar_selection":
                alert("Error: Invalid calendar selection!");
                break;
            case "invalid_user_settings":
                alert("Error: Invalid user settings!");
                break;
            default:
                dashboardErrorHandler(error, "An error occurred reloading your events.");
                break;
        }
        document.getElementById("update-events-button").disabled = false;
    });
}

function saveSettings() {
    let newSettings = {
        calendar_id: document.getElementById("calendar-selector").value,
        completed_assignment_color: document.getElementById("completed-assignment-color-selector").value
    }

    for (const courseSelector of document.getElementById("course-color-selectors").children) {
        if(courseSelector.children.length < 2 || courseSelector.children[1].id === "completed-assignment-color-selector") {
            continue;
        }
        const courseId = courseSelector.children[1].id.replace("course-color-selector-", "");
        newSettings["courses/" + courseId + "/color"] = courseSelector.children[1].value;
    }

    firebase.database().ref("settings/" + user.uid).update(newSettings, error => {
        if(!error) {
            alert("Your settings have been saved!");
            return;
        }
        dashboardErrorHandler(error, "An error occurred saving your settings.");
    });
}

function downloadData() {
    function download(ref, name, description) {
        firebase.database().ref(ref).get().then(snapshot => snapshot.val()).then(data => {
            downloadObjectAsJson(data, name);
        }).catch(error => {
            dashboardErrorHandler(error, "An error occurred downloading your " + description + ".");
        });
    }
    download("auth_status/" + user.uid, "auth-status", "list of signed-in accounts");
    download("settings/" + user.uid, "user-settings", "settings");
}

function deleteAccount() {
    if(!confirm("Are you sure you want to delete your account? This action cannot be undone.")) {
        return;
    }

    user.delete()
    .then(() => {
        alert("Your account has been successfully deleted. You will now be logged out.");
        window.location.href = "/";
    })
    .catch((error) => {
        dashboardErrorHandler(error, "An error occurred deleting your account.");
    });
}
