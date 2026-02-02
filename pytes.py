import pytesseract as pyt
import cv2

img = cv2.imread("ocr_test2.jpeg")

pyt.pytesseract.tesseract_cmd ="C:\\Users\\Jash Jain\\AppData\\Local\\Programs\\Tesseract-OCR\\tesseract.exe"

text = pyt.image_to_string(img)

print(text)