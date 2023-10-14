import {initializeApp} from "firebase-admin/app";
import {getDatabaseWithUrl} from "firebase-admin/database";
import {auth} from "firebase-functions/v1";

initializeApp();

const database_url = "http://127.0.0.1:9000/?ns=coolspy3-gradescope-calendar-default-rtdb";

export const onUserDelete = auth.user().onDelete((user) => {
    console.log(`Deleting user ${user.uid}`);
    return getDatabaseWithUrl(database_url).ref("/").update({
        [`assignments/${user.uid}`]: null,
        [`auth_status/${user.uid}`]: null,
        [`credentials/${user.uid}`]: null,
        [`settings/${user.uid}`]: null,
    });
});
