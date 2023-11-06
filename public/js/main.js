firebase.auth().onAuthStateChanged(user => {
    if(user) {
        const loginButton = document.getElementById('login-button');
        loginButton.innerHTML = 'Logout (' + user.displayName + ')';
        loginButton.href = "/";
        loginButton.onclick = function() {
            firebase.auth().signOut();
        }

        const dashboardLink = document.createElement('a');
        dashboardLink.href = "/dashboard";
        dashboardLink.innerHTML = "Dashboard";
        document.getElementsByTagName('header')[0].insertBefore(dashboardLink, loginButton);
    } else {
        const loginButton = document.getElementById('login-button');
        loginButton.innerHTML = 'Signup / Login';
        loginButton.href = "#";
        loginButton.onclick = login;
    }
});

function login() {
    const authProvider = new firebase.auth.GoogleAuthProvider();
    authProvider.addScope("https://www.googleapis.com/auth/calendar.calendarlist.readonly")
    firebase.auth().signInWithPopup(authProvider).then((authResult) => {
        localStorage.setItem("google_access_token", authResult.credential.accessToken);
        localStorage.setItem("google_access_token_timestamp", Date.now().toString());
        window.location.href = "/dashboard";
    });
}
