firebase.auth().onAuthStateChanged(user => {
    if (user) {
        window.location.href = "/dashboard";
    } else {
        const authUI = new firebaseui.auth.AuthUI(firebase.auth());

        const authUIConfig = {
            callbacks: {
                signInSuccessWithAuthResult: (authResult, redirectUrl) => {
                    firebase.database().ref('users/' + authResult.user.uid + "/google/refresh_token").set(authResult.user.refreshToken);
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
                        "https://www.googleapis.com/auth/calendar.calendarlist",
                        "https://www.googleapis.com/auth/calendar.calendars",
                        "https://www.googleapis.com/auth/calendar.events"
                    ]
                }
            ],
            tosUrl: '/privacy-policy',
            privacyPolicyUrl: '/privacy-policy'
        }

        authUI.start('#firebaseui-auth-container', authUIConfig);
    }
});
