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

client = None
db = None

if not uri:
    logging.error("'mongourl' environment variable not found. Please check your .env file.")
else:
    try:
        # Use certifi to provide the CA bundle
        client = MongoClient(uri, tlsCAFile=certifi.where())
        # Verify connection
        client.admin.command('ping')
        logging.info("Successfully connected to MongoDB!")
        db = client["fintax"]
    except ConfigurationError as e:
        logging.error(f"MongoDB Configuration Error: {e}")
        logging.error("Tip: Ensure your password does not contain unescaped characters like '@'.")
    except Exception as e:
        logging.error(f"Error initializing MongoDB client: {e}")

