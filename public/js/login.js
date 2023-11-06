authUIRendered = false;

firebase.auth().onAuthStateChanged(user => {
    if(authUIRendered) return; // onAuthStateChanged will be called again when the user signs in, so set a flag to prevent the code from running twice
    authUIRendered = true;
    if (user) {
        window.location.href = "/dashboard";
    } else {
        const authProvider = new firebase.auth.GoogleAuthProvider();
        authProvider.addScope("https://www.googleapis.com/auth/calendar.calendarlist.readonly")
        firebase.auth().signInWithPopup(authProvider).then((authResult) => {
            localStorage.setItem("google_access_token", authResult.credential.accessToken);
            localStorage.setItem("google_access_token_timestamp", Date.now().toString());
            if(authResult.credential.accessToken === "FirebaseAuthEmulatorFakeAccessToken_google.com") {
                // We're running in a testing environment. This account is not real. Redirect to the dashboard and skip backend google auth.
                window.location.href = "/dashboard";
                return;
            }
            firebase.database().ref("auth_status/" + authResult.user.uid).get().then(snapshot => snapshot.val()).then(authStatus => {
                if (!authStatus) {
                    alert("It looks like this is your first time logging in or we lost the ability to access your Google Calendar." +
                        "We will now try to link your Google account to our backend. Google may prompt you to sign in again.");

                    function onError(error) {
                        alert("An error occurred linking your Google account!");
                        console.error(error);
                        firebase.auth().signOut();
                        window.location.href = "/";
                    }

                    const oauth2Client = google.accounts.oauth2.initCodeClient({
                        client_id: "1008979285844-jmod7piqutf0odtvnu8eohd0vmo2dlb3.apps.googleusercontent.com",
                        scope: "https://www.googleapis.com/auth/calendar.calendarlist.readonly https://www.googleapis.com/auth/calendar.events",
                        ux_mode: "popup",
                        callback: (response) => {
                            firebase.functions().httpsCallable("oauth_callback")({code: response.code}).then(result => {
                                if (result.data.success) {
                                    window.location.href = "/dashboard";
                                } else {
                                    onError(result.data);
                                }
                            }).catch(onError);
                        },
                        error_callback: onError
                    });

                    oauth2Client.requestCode();
                } else {
                    window.location.href = "/dashboard";
                }
            });
        });
    }
});
