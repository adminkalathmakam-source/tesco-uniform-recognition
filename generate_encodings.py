import cv2
import face_recognition
import pickle
import os

def generate_encodings():
    folder_path = 'employees'
    encodings_list = []
    names_list = []
    
    if not os.path.exists(folder_path):
        print(f"Error: '{folder_path}' directory not found.")
        return

    print("Starting encoding generation...")
    
    path_list = os.listdir(folder_path)
    print(f"Found {len(path_list)} images.")

    for path in path_list:
        if path.lower().endswith(('.png', '.jpg', '.jpeg')):
            img_path = os.path.join(folder_path, path)
            img = cv2.imread(img_path)
            if img is None:
                print(f"Failed to load image: {path}")
                continue
                
            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            
            # Get encodings
            encodings = face_recognition.face_encodings(img_rgb)
            
            if encodings:
                encodings_list.append(encodings[0])
                name = os.path.splitext(path)[0]
                names_list.append(name)
                print(f"Encoded: {name}")
            else:
                print(f"No face found in: {path}")
    
    print(f"Total encoded: {len(encodings_list)}")
    
    data = {"encodings": encodings_list, "names": names_list}
    
    with open("face_encodings.pkl", "wb") as f:
        pickle.dump(data, f)
    
    print("Encodings saved to face_encodings.pkl")

if __name__ == "__main__":
    generate_encodings()
