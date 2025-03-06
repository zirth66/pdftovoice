from PIL import Image, ImageDraw
import os

# Create a simple icon with a centered letter
def create_icon(size, output_file):
    # Create a blue square
    img = Image.new('RGB', (size, size), color=(13, 110, 253))  # Bootstrap primary blue
    draw = ImageDraw.Draw(img)
    
    # Add a white circle in the center
    circle_radius = size // 3
    circle_position = ((size - 2 * circle_radius) // 2, (size - 2 * circle_radius) // 2)
    circle_end = (circle_position[0] + 2 * circle_radius, circle_position[1] + 2 * circle_radius)
    draw.ellipse([circle_position, circle_end], fill=(255, 255, 255))
    
    # Save the image
    img.save(output_file)
    print(f"Created {output_file}")

# Create icons of different sizes
create_icon(192, 'icon-192x192.png')
create_icon(512, 'icon-512x512.png')

print("PWA icons generated successfully.") 