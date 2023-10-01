firebase.auth().onAuthStateChanged(user => {
    if (user) {
        window.location.href = "/dashboard";
    } else {
        const authUI = new firebaseui.auth.AuthUI(firebase.auth());

        const authUIConfig = {
            callbacks: {
                signInSuccessWithAuthResult: (authResult, redirectUrl) => {
                    gapi.load('client', () => gapi.client.setToken({access_token: authResult.credentials.accessToken}));
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
                                client_id: "<CLIENT_ID>",
                                scope: "https://www.googleapis.com/auth/calendar.calendarlist.readonly https://www.googleapis.com/auth/calendar.events",
                                ux_mode: "popup",
                                callback: (response) => {
                                    firebase.functions().httpsCallable("update_google_token")({code: response.code}).then(result => {
                                        if (result.data.success) {
                                            window.location.href = "/dashboard";
                                        } else {
                                            onError(result.data);
                                        }
                                    }).catch(onError);
                                },
                                error_callback: onError
                            });
                        }
                    });
                    return true;
                },
                uiShown: () => {
                    document.getElementById('loader').style.display = 'none';
                }
            },
            signInFlow: 'popup',
            signInSuccessUrl: '/dashboard',
            signInOptions: [
                {
                    provider: firebase.auth.GoogleAuthProvider.PROVIDER_ID,
                    scopes: [
                        "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
                    ]
                }
            ],
            tosUrl: '/privacy-policy',
            privacyPolicyUrl: '/privacy-policy'
        }

        authUI.start('#firebaseui-auth-container', authUIConfig);
    }
});
