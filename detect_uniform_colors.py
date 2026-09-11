import cv2
import numpy as np
from pathlib import Path
import os

def detect_dominant_colors(image_path, num_colors=3):
    """
    Detect dominant colors in an image and generate HSV ranges
    """
    print(f"Analyzing uniform photo: {image_path}")
    
    # Read image    
    img = cv2.imread(str(image_path))
    if img is None:
        print(f"Error: Could not read image {image_path}")
        return None
    
    # Convert to HSV
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    
    # Reshape image to be a list of pixels
    pixels = hsv.reshape((-1, 3))
    pixels = np.float32(pixels)
    
    # Use K-means clustering to find dominant colors
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 0.2)
    k = num_colors
    _, labels, centers = cv2.kmeans(pixels, k, None, criteria, 10, cv2.KMEANS_RANDOM_CENTERS)
    
    # Convert centers back to uint8
    centers = np.uint8(centers)
    
    # Count pixels in each cluster
    unique, counts = np.unique(labels, return_counts=True)
    
    # Sort by frequency
    sorted_indices = np.argsort(-counts)
    
    print(f"\nDetected {num_colors} dominant colors:")
    print("="*60)
    
    color_configs = []
    
    for i, idx in enumerate(sorted_indices):
        h, s, v = centers[idx]
        percentage = (counts[idx] / len(labels)) * 100
        
        # Create color name based on hue
        color_name = get_color_name(h)
        
        # Generate HSV range with some tolerance
        h_tolerance = 15
        s_tolerance = 50
        v_tolerance = 50
        
        lower = [
            max(0, int(h) - h_tolerance),
            max(0, int(s) - s_tolerance),
            max(0, int(v) - v_tolerance)
        ]
        upper = [
            min(180, int(h) + h_tolerance),
            min(255, int(s) + s_tolerance),
            min(255, int(v) + v_tolerance)
        ]
        
        print(f"\nColor {i+1}: {color_name}")
        print(f"  Coverage: {percentage:.1f}%")
        print(f"  HSV Center: H={h}, S={s}, V={v}")
        print(f"  Suggested Range:")
        print(f"    'lower': {lower}")
        print(f"    'upper': {upper}")
        
        color_configs.append({
            'name': color_name,
            'lower': lower,
            'upper': upper,
            'percentage': percentage
        })
    
    return color_configs

def get_color_name(hue):
    """Convert HSV hue to color name"""
    if hue < 10 or hue > 170:
        return "Red"
    elif hue < 25:
        return "Orange"
    elif hue < 35:
        return "Yellow"
    elif hue < 85:
        return "Green"
    elif hue < 130:
        return "Blue"
    elif hue < 160:
        return "Purple"
    else:
        return "Pink"

def generate_config_file(color_configs):
    """Generate uniform_config.py from detected colors"""
    
    config_content = """# Uniform Color Configuration
# Auto-generated from uniform photo analysis

# Define your uniform color ranges
UNIFORM_COLORS = [
"""
    
    for config in color_configs:
        config_content += f"""    {{
        'name': '{config['name']}',
        'lower': {config['lower']},
        'upper': {config['upper']}
    }},
"""
    
    config_content += """]

# Minimum percentage of uniform colors needed to consider someone wearing uniform
# Adjust this value based on testing (lower = more lenient, higher = stricter)
UNIFORM_THRESHOLD = 30  # 30% of torso area should match uniform colors
"""
    
    with open('uniform_config.py', 'w') as f:
        f.write(config_content)
    
    print("\n" + "="*60)
    print("[SUCCESS] Generated uniform_config.py successfully!")
    print("="*60)

def main():
    """Main function to detect uniform colors from photos"""
    
    uni_folder = Path("uni")
    
    if not uni_folder.exists():
        print("Error: 'uni' folder not found!")
        print("Please create a 'uni' folder and add a photo of your uniform.")
        return
    
    # Find image files in uni folder
    image_extensions = ['.jpg', '.jpeg', '.png', '.JPG', '.JPEG', '.PNG']
    image_files = [f for f in uni_folder.iterdir() 
                   if f.is_file() and f.suffix in image_extensions]
    
    if not image_files:
        print("Error: No images found in 'uni' folder!")
        print("Please add a photo of your uniform to the 'uni' folder.")
        return
    
    # Use the first image found
    uniform_image = image_files[0]
    
    print("="*60)
    print("UNIFORM COLOR DETECTION")
    print("="*60)
    
    # Detect colors
    color_configs = detect_dominant_colors(uniform_image, num_colors=3)
    
    if color_configs:
        # Generate config file
        generate_config_file(color_configs)
        
        print("\nNext steps:")
        print("1. Review uniform_config.py")
        print("2. Adjust color ranges if needed")
        print("3. Run: python main.py")

if __name__ == "__main__":
    main()
