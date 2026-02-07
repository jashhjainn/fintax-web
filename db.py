from pymongo import MongoClient
from pymongo.errors import ConfigurationError
import os
from dotenv import load_dotenv
import sys
import certifi
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, filename='app.log', filemode='a', 
                    format='%(asctime)s - %(levelname)s - %(message)s')

load_dotenv()

uri = os.getenv("mongourl")
local_uri = os.getenv("mongo_local", "mongodb://127.0.0.1:27017")

client = None
db = None

def _connect(uri_value: str, use_tls: bool):
    if use_tls:
        return MongoClient(uri_value, tlsCAFile=certifi.where())
    return MongoClient(uri_value)

try:
    if not uri:
        logging.error("'mongourl' environment variable not found. Falling back to local MongoDB.")
        client = _connect(local_uri, use_tls=False)
    else:
        client = _connect(uri, use_tls=True)

    client.admin.command('ping')
    logging.info("Successfully connected to MongoDB!")
    db = client["fintax"]
except ConfigurationError as e:
    logging.error(f"MongoDB Configuration Error: {e}")
    logging.error("Tip: Ensure your password does not contain unescaped characters like '@'.")
except Exception as e:
    logging.error(f"Error initializing MongoDB client: {e}")
    # Fallback to local when Atlas/TLS fails (useful for localhost dev)
    if uri:
        try:
            logging.info("Falling back to local MongoDB after remote connection failure.")
            client = _connect(local_uri, use_tls=False)
            client.admin.command('ping')
            logging.info("Successfully connected to local MongoDB!")
            db = client["fintax"]
        except Exception as e2:
            logging.error(f"Local MongoDB connection also failed: {e2}")

