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
    }
});
