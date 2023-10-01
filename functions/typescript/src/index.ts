import {initializeApp, database} from "firebase-admin";
import {auth} from "firebase-functions/v1";

initializeApp();

export const onUserDelete = auth.user().onDelete((user) => {
    return database().ref("/").update({
        [`assignments/${user.uid}`]: null,
        [`auth_status/${user.uid}`]: null,
        [`credentials/${user.uid}`]: null,
        [`settings/${user.uid}`]: null,
    });
});
