# Uniform Color Configuration
# Auto-calibrated from sharp uniform reference image (IMG_2776.jpeg)
# Uniform: Navy Blue blazer/vest + Navy trousers + White shirt + Blue/striped tie

# ---------------------------------------------------------
# Navy Blue - Main uniform body (blazer, vest, trousers)
# Covers dark navy to mid navy, low/high brightness variants
# ---------------------------------------------------------
UNIFORM_COLORS = [
    {
        'name': 'Navy Blue Dark',
        'lower': [98, 80, 0],
        'upper': [128, 255, 120]
    },
    {
        'name': 'Navy Blue Mid',
        'lower': [98, 30, 100],
        'upper': [128, 200, 200]
    },
    {
        'name': 'Blue Tie/Lanyard',
        'lower': [95, 100, 40],
        'upper': [130, 255, 220]
    },
]

# Minimum percentage of uniform colors in the torso region to count as wearing uniform
# Lowered slightly to improve detection reliability under varied lighting
UNIFORM_THRESHOLD = 35   # 35% of torso area should show navy/blue tones
MALE_THRESHOLD_EXTRA = 10  # Extra % for male students (tie adds blue area)
