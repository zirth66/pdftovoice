from PIL import Image, ImageDraw, ImageFont
import os

# Create a simple icon with "P2V" text
def create_icon(size, output_file):
    # Create a blue square with rounded corners
    img = Image.new('RGB', (size, size), color = (13, 110, 253))  # Bootstrap primary blue
    draw = ImageDraw.Draw(img)
    
    # Add text
    try:
        font_size = size // 3
        font = ImageFont.truetype("arial.ttf", font_size)
    except IOError:
        # Fall back to default font if arial is not available
        font = ImageFont.load_default()
    
    text = "P2V"
    text_width, text_height = draw.textsize(text, font=font)
    position = ((size-text_width)//2, (size-text_height)//2)
    
    # Draw white text
    draw.text(position, text, (255, 255, 255), font=font)
    
    # Save the image
    img.save(output_file)
    print(f"Created {output_file}")

# Create icons of different sizes
create_icon(192, 'icon-192x192.png')
create_icon(512, 'icon-512x512.png')

print("PWA icons generated successfully.") 