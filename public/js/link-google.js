let uiRendered = false;

firebase.auth().onAuthStateChanged((user) => {
    if(uiRendered) return; // onAuthStateChanged will be called again when the user signs in, so set a flag to prevent the code from running twice
    uiRendered = true;
    if (user) {
        firebase.database().ref("auth_status/" + user.uid + "/google").get().then(ref => ref.val())
        .then(isAuthValid => {
            if(isAuthValid) {
                window.location.href = "/dashboard";
            } else {
                // Disable the button by default, so we can check the auth status before giving the user the ability to link their account.
                const linkGoogleButton = document.getElementById("link-google-button");
                linkGoogleButton.disabled = false;
            }
        }).catch(error => {
            console.log(error);
            alert("An error occurred checking your Google account status! Try refreshing the page.");
        });
    } else {
        window.location.href = "/401";
    }
});

function linkGoogleAccount() {
    function onError(error) {
        alert("An error occurred linking your Google account!");
        console.error(error);
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
}
