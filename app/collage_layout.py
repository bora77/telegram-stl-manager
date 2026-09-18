"""Deterministic collage geometry and rendering; never crop source images."""
from itertools import combinations
import math
from PIL import Image, ImageDraw, ImageFont, ImageOps

FORMATS = {'landscape': (3840, 2160), 'portrait': (2160, 3840), 'square': (3000, 3000)}
LAYOUTS = ('featured', 'rows', 'grid')
COLORS = {'dark': ('#15161a', '#090a0c', '#f1f1f4'), 'light': ('#f5f4f8', '#ffffff', '#272332')}


def _rows(ratios, indices, area, gap):
    """Fit contiguous rows while keeping every selected image readable."""
    x0, y0, x1, y1 = area
    width, height = x1-x0, y1-y0
    best = None
    for count in range(1, min(len(indices), 5)+1):
        for cuts in combinations(range(1, len(indices)), count-1):
            edges = (0, *cuts, len(indices))
            rows = [indices[a:b] for a, b in zip(edges, edges[1:])]
            heights = [(width-gap*(len(row)-1))/sum(ratios[i] for i in row) for row in rows]
            scale = min(1, (height-gap*(count-1))/sum(heights))
            if scale <= 0:continue
            # Geometric mean of image areas avoids one huge image winning at
            # the expense of a row of tiny images, especially in portrait output.
            score = sum(math.log((h*scale)**2*ratios[i]) for row, h in zip(rows, heights) for i in row)
            if best is None or score > best[0]:best = score, rows, heights, scale
    _, rows, heights, scale = best
    y = y0+(height-sum(heights)*scale-gap*(len(rows)-1))/2
    cells = []
    for row, h in zip(rows, heights):
        h *= scale
        widths = [h*ratios[i] for i in row]
        x = x0+(width-sum(widths)-gap*(len(row)-1))/2
        for i, w in zip(row, widths):
            cells.append((i, (x, y, x+w, y+h)))
            x += w+gap
        y += h+gap
    return cells


def geometry(sizes, layout='featured', shape='landscape', title=True):
    if not 2 <= len(sizes) <= 9 or any(w<=0 or h<=0 for w,h in sizes):raise ValueError('A collage needs 2–9 valid image slots.')
    width, height = FORMATS[shape]
    margin = min(width, height)/30
    gap = margin/3
    area = (margin, margin*3 if title else margin, width-margin, height-margin)
    x0, y0, x1, y1 = area
    ratios = [w/h for w, h in sizes]
    indices = list(range(len(sizes)))
    if layout == 'rows':return _rows(ratios, indices, area, gap)
    if layout == 'featured':
        # Portrait leads sit to the left; landscape leads sit above the others.
        if ratios[0] < 1:
            lead_width = min((y1-y0)*ratios[0], (x1-x0)*.53)
            cells = [(0, (x0, y0, x0+lead_width, y1))]
            rest = (x0+lead_width+gap, y0, x1, y1)
        else:
            lead_height = min((x1-x0)/ratios[0], (y1-y0)*.53)
            cells = [(0, (x0, y0, x1, y0+lead_height))]
            rest = (x0, y0+lead_height+gap, x1, y1)
        return cells+_rows(ratios, indices[1:], rest, gap)
    # Select an equal-cell grid that wastes the least space for this selection.
    options = []
    for columns in range(1, len(sizes)+1):
        rows = math.ceil(len(sizes)/columns)
        cw, ch = (x1-x0-gap*(columns-1))/columns, (y1-y0-gap*(rows-1))/rows
        score = sum(min(cw/w, ch/h)**2*w*h for w, h in sizes)
        options.append((score, columns, rows, cw, ch))
    _, columns, rows, cw, ch = max(options)
    cells = []
    for i in indices:
        row, col = divmod(i, columns)
        count = min(columns, len(sizes)-row*columns)
        left = x0+(x1-x0-count*cw-gap*(count-1))/2
        x, y = left+col*(cw+gap), y0+row*(ch+gap)
        cells.append((i, (x, y, x+cw, y+ch)))
    return cells


def render(sources, settings, destination, *, preview=False):
    """Sources contain dimensions and an opener returning a verified byte stream."""
    shape, layout, theme = settings['shape'], settings['layout'], settings['theme']
    width, height = FORMATS[shape]
    scale = min(1280/width, 1280/height) if preview else 1
    canvas = Image.new('RGB', (round(width*scale), round(height*scale)), COLORS[theme][0])
    cells = geometry([(s['width'], s['height']) for s in sources], layout, shape, bool(settings['title']))
    warnings = []
    for i, rect in cells:
        source = sources[i]
        cell = tuple(round(v*scale) for v in rect)
        size = (max(1, cell[2]-cell[0]), max(1, cell[3]-cell[1]))
        if min((rect[2]-rect[0])/source['width'], (rect[3]-rect[1])/source['height']) > 1.1:
            warnings.append(source['name']+' has limited resolution for its frame.')
        with source['open']() as stream, Image.open(stream) as original:
            image = ImageOps.exif_transpose(original)
            try:
                tile = Image.new('RGB', size, COLORS[theme][1])
                fitted = ImageOps.contain(image, size, Image.Resampling.LANCZOS)
                rgba = fitted.convert('RGBA');fitted.close()
                try:tile.paste(rgba, ((size[0]-rgba.width)//2, (size[1]-rgba.height)//2), rgba)
                finally:rgba.close()
                canvas.paste(tile, cell[:2]);tile.close()
            finally:image.close()
    if settings['title']:
        draw = ImageDraw.Draw(canvas)
        margin = round(min(width, height)/30*scale)
        font_size = round(min(width, height)/30*scale)
        while True:
            font = ImageFont.truetype('DejaVuSans.ttf', max(10, font_size))
            if draw.textbbox((0,0), settings['title'], font=font)[2] <= canvas.width-2*margin or font_size <= 10:break
            font_size -= 2
        draw.text((margin, margin*.8), settings['title'], fill=COLORS[theme][2], font=font)
    canvas.save(destination, 'JPEG', quality=90 if preview else 95, subsampling=0)
    canvas.close()
    return {'width': round(width*scale), 'height': round(height*scale), 'warnings': warnings}
