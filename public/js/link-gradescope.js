function gradescopeLinkerErrorHandler(data, msg) {
    alert(msg + " Please contact the developer if this problem continues. Helpful debug information has been dumped to the console.");
    console.error(msg + "\n" )
    console.log("BEGIN HELPFUL DEBUG INFORMATION");
    console.log(data);
    console.log(JSON.stringify(data));
    console.log("END HELPFUL DEBUG INFORMATION");
}

firebase.auth().onAuthStateChanged((user) => {
    if (!user) {
        window.location.href = "/login";
    }
});

function linkByToken() {
    document.getElementById("token-link-parameters").hidden = false;
    document.getElementById("password-link-parameters").hidden = true;
}

function linkByPassword() {
    document.getElementById("token-link-parameters").hidden = true;
    document.getElementById("password-link-parameters").hidden = false;
}

function validateGradescopeCredentials() {
    document.getElementById("link-gradescope-button").disabled = true;
    const linkByToken = document.getElementById("link-by-token").checked;
    const linkByPassword = document.getElementById("link-by-password").checked;

    if ((linkByToken + linkByPassword) !== 1) {
        alert("Please Choose a Linking Method!");
        return;
    }

    if(linkByToken) {
        const token = document.getElementById("token").value;

        if (token === "") {
            alert("Please Enter a Token!");
            return;
        }

        firebase.functions().httpsCallable("update_gradescope_token")({token: token}).then(result => {
            if (result.data.success) {
                alert("Successfully Linked to Gradescope!");
                window.location.href = "/dashboard";
            } else {
                alert("A backend error occurred linking your Gradescope account!");
                document.getElementById("link-gradescope-button").disabled = false;
            }
        }).catch(error => {
            if(error.message === "invalid_gradescope_auth") {
                alert("Invalid Token!");
            } else {
                gradescopeLinkerErrorHandler(error, "An error occurred while validating your token!");
            }
            document.getElementById("link-gradescope-button").disabled = false;
        });
    } else {
        const email = document.getElementById("email").value;
        const password = document.getElementById("password").value;
        const storeCredentials = document.getElementById("store-credentials").checked;

        if (email === "") {
            alert("Please Enter an Email!");
            return;
        }

        if (password === "") {
            alert("Please Enter a Password!");
            return;
        }

        firebase.functions().httpsCallable("update_gradescope_token")({email: email, password: password, storeCredentials: storeCredentials}).then((result) => {
            if (result.data.success) {
                alert("Successfully Linked to Gradescope!");
                window.location.href = "/dashboard";
            } else {
                alert("A backend error occurred linking your Gradescope account!");
                document.getElementById("link-gradescope-button").disabled = false;
            }
        }).catch(error => {
            if(error.message === "invalid_gradescope_auth") {
                alert("Invalid Credentials!");
            } else {
                gradescopeLinkerErrorHandler(error, "An error occurred while validating your token!");
            }
            document.getElementById("link-gradescope-button").disabled = false;
        });
    }
}
