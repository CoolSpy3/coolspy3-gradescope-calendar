import {initializeApp} from "firebase-admin/app";
import {getDatabase} from "firebase-admin/database";
import {auth} from "firebase-functions/v1";

initializeApp();

export const onUserDelete = auth.user().onDelete((user) => {
    console.log(`Deleting user ${user.uid}`);
    return getDatabase().ref("/").update({
        [`assignments/${user.uid}`]: null,
        [`auth_status/${user.uid}`]: null,
        [`credentials/${user.uid}`]: null,
        [`settings/${user.uid}`]: null,
    });
});
