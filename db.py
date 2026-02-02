import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore

# 1. Initialize the SDK using your JSON key
cred = credentials.Certificate("firebase.json")
firebase_admin.initialize_app(cred)

# 2. Connect to Firestore
db = firestore.client()

print("Connection successfull!")