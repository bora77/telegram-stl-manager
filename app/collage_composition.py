"""Lightweight composition clues for studio renders, using Pillow only."""
from collections import Counter, deque

from PIL import Image, ImageChops, ImageFilter, ImageOps


def _components(mask):
    width, height = mask.size
    data = bytearray(mask.tobytes())
    components = []
    for start in range(len(data)):
        if not data[start]:
            continue
        data[start] = 0
        queue = deque([start])
        min_x = max_x = start % width
        min_y = max_y = start // width
        area = 0
        while queue:
            current = queue.popleft()
            y, x = divmod(current, width)
            min_x, max_x = min(min_x, x), max(max_x, x)
            min_y, max_y = min(min_y, y), max(max_y, y)
            area += 1
            for row in range(max(0, y-1), min(height, y+2)):
                for col in range(max(0, x-1), min(width, x+2)):
                    index = row*width+col
                    if data[index]:
                        data[index] = 0
                        queue.append(index)
        if area >= width*height*.00035:
            components.append((max_x-min_x+1, max_y-min_y+1, area))
    return components


def composition(image):
    image = ImageOps.exif_transpose(image).convert('RGB')
    image.thumbnail((400, 400))
    width, height = image.size
    if min(width, height) < 40:
        return {'group_candidate': False, 'detail_sheet': False, 'reason': 'Image too small for composition analysis'}
    # Branding strips are common on release renders. Restrict this weak visual
    # heuristic to the central canvas, preserving the source image for rendering.
    image = image.crop((int(width*.025), int(height*.10), int(width*.975), int(height*.89)))
    width, height = image.size
    borders = []
    for box in ((0,0,width,3),(0,height-3,width,height),(0,0,3,height),(width-3,0,width,height)):
        borders.extend(image.crop(box).getdata())
    key = Counter(tuple(value//32 for value in pixel) for pixel in borders).most_common(1)[0][0]
    cluster = [pixel for pixel in borders if tuple(value//32 for value in pixel) == key]
    background = tuple(sorted(pixel[c] for pixel in cluster)[len(cluster)//2] for c in range(3))
    agreement = sum(max(abs(v-b) for v,b in zip(pixel, background))<32 for pixel in borders)/len(borders)
    if agreement < .45:
        return {'group_candidate': False, 'detail_sheet': False, 'reason': 'Background is too varied to estimate subjects reliably'}
    channels = ImageChops.difference(image, Image.new('RGB', image.size, background)).split()
    difference = ImageChops.lighter(ImageChops.lighter(channels[0],channels[1]),channels[2])
    mask = difference.point(lambda v: 255 if v>40 else 0).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    all_parts = _components(mask)
    parts = [p for p in all_parts if p[2]>=width*height*.002]
    main = [p for p in parts if p[2]>=width*height*.012 and p[1]>=height*.16 and p[0]>=width*.075]
    total = sum(p[2] for p in parts)
    largest = max((p[2] for p in parts),default=0)/max(1,total)
    small_area=sum(p[2] for p in all_parts if p[2]<width*height*.002)
    fragmented = len(parts)>12 or (len(all_parts)>25 and small_area>sum(p[2] for p in all_parts)*.35)
    dominant_with_parts = len(main)>=3 and largest>.60
    group = 3<=len(main)<=10 and largest<.58 and not fragmented
    detail = fragmented or dominant_with_parts
    reason = ('Several sizeable foreground shapes suggest a group/overview image' if group else
              'Many small pieces or one dominant figure with separate parts suggest a detail sheet' if detail else
              'No strong group-shot evidence; review the featured image')
    return {'group_candidate':group,'detail_sheet':detail,'subjects':len(main),
            'fragments':len(parts),'largest_fraction':round(largest,3),
            'score':round(sum(min(p[2]/(width*height*.08),1) for p in main),3) if group else 0,
            'reason':reason}
