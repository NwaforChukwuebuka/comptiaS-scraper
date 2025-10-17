#!/usr/bin/env python3

import argparse
import json
import os
import re
import time
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from fpdf import FPDF
from PIL import Image
from tqdm import tqdm


BASE_URL = "https://free-braindumps.com"
SECTION_PATH = "/comptia/free-sy0-701-braindumps/page-{}"

# HTTP session with retry-friendly settings
SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": BASE_URL,
    }
)


@dataclass
class QAItem:
    page_number: int
    question_number_on_page: int
    question_text: str
    options: List[str]
    correct_answer_letter: Optional[str]
    answer_text: Optional[str]
    explanation_text: Optional[str]
    question_images: List[str]
    explanation_images: List[str]


def ensure_dir(path: str) -> None:
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)


def sanitize_filename(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    # collapse consecutive underscores
    return re.sub(r"_+", "_", name).strip("._")


def fetch_html(url: str, max_retries: int = 3, timeout: int = 30) -> Optional[str]:
    for attempt in range(1, max_retries + 1):
        try:
            resp = SESSION.get(url, timeout=timeout)
            if resp.status_code == 200 and "text/html" in resp.headers.get("Content-Type", ""):
                return resp.text
        except requests.RequestException:
            pass
        time.sleep(1.5 * attempt)
    return None


def is_absolute_url(url: str) -> bool:
    try:
        return bool(urlparse(url).scheme)
    except Exception:
        return False


def collect_images(panel_body: BeautifulSoup) -> Dict[str, List[str]]:
    # Separate images within explanation block if present
    question_images: List[str] = []
    explanation_images: List[str] = []

    # First, collect all images in the panel body
    all_imgs = panel_body.select("img")
    
    # Check if there's an explanation block
    exp_block = panel_body.select_one("div.bg-light-yellow")
    
    for img in all_imgs:
        src = img.get("src")
        if not src:
            continue
            
        # Check if this image is inside the explanation block
        if exp_block and img in exp_block.find_all("img"):
            explanation_images.append(src)
        else:
            question_images.append(src)

    return {"question_images": question_images, "explanation_images": explanation_images}


def extract_options_and_correct(panel_body: BeautifulSoup) -> (List[str], Optional[str]):
    options: List[str] = []
    correct_letter: Optional[str] = None

    ol = panel_body.select_one("ol.rounded-list")
    if not ol:
        return options, correct_letter

    # Items are ordered A, B, C ... by type="A" in <ol>
    li_list = ol.select("li")
    for idx, li in enumerate(li_list):
        text = li.get_text(" ", strip=True)
        options.append(text)
        data_correct = li.get("data-correct")
        classes = li.get("class", [])
        if (data_correct and data_correct.lower() == "true") or ("correct" in classes):
            correct_letter = chr(ord("A") + idx)

    return options, correct_letter


def extract_answer_and_explanation(panel_body: BeautifulSoup) -> (Optional[str], Optional[str]):
    answer_text: Optional[str] = None
    explanation_text: Optional[str] = None

    # Answer area is in a collapsed div with id like answerQ1
    answer_div = None
    for div in panel_body.select("div[id]"):
        if div.get("id", "").startswith("answerQ"):
            answer_div = div
            break

    if answer_div:
        # Typical content: <p><strong>Answer(s):</strong> B <br></p>
        p = answer_div.find("p")
        if p:
            answer_text = p.get_text(" ", strip=True)
            # normalize like "Answer(s): B" only
            m = re.search(r"Answer\(s\):\s*(.+)$", answer_text)
            if m:
                answer_text = m.group(1).strip()

        # Explanation may be in a sibling div with class bg-light-yellow
        exp = answer_div.select_one(".bg-light-yellow")
        if exp:
            # Remove the bold label if exists
            explabel = exp.find("strong")
            if explabel and "Explanation" in explabel.get_text():
                explabel.extract()
            explanation_text = exp.get_text(" ", strip=True)

    # Fallback: explanation sometimes lives elsewhere in panel-body
    if explanation_text is None:
        exp = panel_body.select_one(".bg-light-yellow")
        if exp:
            strong = exp.find("strong")
            if strong and "Explanation" in strong.get_text():
                strong.extract()
            explanation_text = exp.get_text(" ", strip=True)

    return answer_text, explanation_text


def parse_questions_from_page(html: str, page_number: int) -> List[QAItem]:
    soup = BeautifulSoup(html, "lxml")
    qa_items: List[QAItem] = []

    panels = soup.select("div.panel.panel-default")
    q_index = 0
    for panel in panels:
        heading = panel.select_one(".panel-heading h4")
        body = panel.select_one(".panel-body")
        if not body:
            continue

        q_text_el = body.select_one("p.lead")
        if not q_text_el:
            # Not a quiz panel
            continue

        q_index += 1
        question_text = q_text_el.get_text(" ", strip=True)

        options, correct_letter = extract_options_and_correct(body)
        answer_text, explanation_text = extract_answer_and_explanation(body)
        images = collect_images(body)

        qa_items.append(
            QAItem(
                page_number=page_number,
                question_number_on_page=q_index,
                question_text=question_text,
                options=options,
                correct_answer_letter=correct_letter,
                answer_text=answer_text,
                explanation_text=explanation_text,
                question_images=images["question_images"],
                explanation_images=images["explanation_images"],
            )
        )

    return qa_items


def download_image(url: str, images_dir: str, prefix: str) -> Optional[str]:
    try:
        abs_url = url if is_absolute_url(url) else urljoin(BASE_URL, url)
        ext = os.path.splitext(urlparse(abs_url).path)[1] or ".jpg"
        filename = sanitize_filename(f"{prefix}{ext}")
        dest = os.path.join(images_dir, filename)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            return dest
        with SESSION.get(abs_url, stream=True, timeout=45) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        return dest
    except Exception:
        return None


class PDFBuilder(FPDF):
    def __init__(self, title: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.title = title
        self.set_auto_page_break(auto=True, margin=15)
        self.set_margins(15, 15, 15)
        self.alias_nb_pages()

    def header(self):
        self.set_font("Helvetica", "B", 12)
        self.cell(0, 10, self.title, ln=1, align="C")
        self.ln(2)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", size=9)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def clean_text(self, text: str) -> str:
        """Clean text to remove problematic Unicode characters"""
        # Replace common problematic Unicode characters
        replacements = {
            '\u2019': "'",  # Right single quotation mark
            '\u2018': "'",  # Left single quotation mark
            '\u201c': '"',  # Left double quotation mark
            '\u201d': '"',  # Right double quotation mark
            '\u2013': '-',  # En dash
            '\u2014': '--', # Em dash
            '\u2026': '...', # Horizontal ellipsis
            '\u00a0': ' ',  # Non-breaking space
        }
        
        for unicode_char, replacement in replacements.items():
            text = text.replace(unicode_char, replacement)
        
        # Remove any remaining non-ASCII characters
        try:
            text.encode('latin-1')
            return text
        except UnicodeEncodeError:
            # If still problematic, encode as ASCII with errors='replace'
            return text.encode('ascii', errors='replace').decode('ascii')

    def add_wrapped_text(self, text: str, bold: bool = False, size: int = 11, ln: bool = True):
        style = "B" if bold else ""
        self.set_font("Helvetica", style=style, size=size)
        cleaned_text = self.clean_text(text)
        self.multi_cell(0, 6, cleaned_text)
        if ln:
            self.ln(1)

    def add_image_scaled(self, path: str, max_width_mm: Optional[float] = None):
        if not os.path.isfile(path):
            print(f"    Image file not found: {path}")
            return
        try:
            img = Image.open(path)
            img_width, img_height = img.size
            print(f"    Image dimensions: {img_width}x{img_height}")
            
            # Available width on page
            if max_width_mm is None:
                max_width_mm = self.w - self.l_margin - self.r_margin
            
            # Convert pixels to mm assuming 96 dpi -> 1 inch = 25.4 mm
            dpi = img.info.get("dpi", (96, 96))[0]
            if isinstance(dpi, tuple):
                dpi = dpi[0]
            width_mm = img_width / dpi * 25.4
            height_mm = img_height / dpi * 25.4
            
            print(f"    Calculated size: {width_mm:.1f}x{height_mm:.1f}mm")
            
            if width_mm > max_width_mm:
                scale = max_width_mm / width_mm
                width_mm *= scale
                height_mm *= scale
                print(f"    Scaled to: {width_mm:.1f}x{height_mm:.1f}mm")
            
            # Check if we need a new page
            space_needed = height_mm + 5  # 5mm buffer
            if self.get_y() + space_needed > self.h - self.b_margin:
                print(f"    Not enough space, adding new page")
                self.add_page()
            
            x = self.get_x()
            y = self.get_y()
            print(f"    Adding image at position: x={x}, y={y}")
            
            self.image(path, x=x, y=y, w=width_mm)
            self.ln(height_mm + 2)
            print(f"    Image added successfully")
        except Exception as e:
            print(f"    Error adding image {path}: {e}")
            # If anything goes wrong, skip image rendering
            pass

    def add_images_side_by_side(self, image_paths: List[str], images_dir: str, max_width_mm: Optional[float] = None):
        """Add multiple images side by side to save space, with better sizing"""
        if not image_paths:
            return
            
        if max_width_mm is None:
            max_width_mm = self.w - self.l_margin - self.r_margin
        
        # Calculate dimensions for all images first
        image_data = []
        total_width = 0
        max_height = 0
        
        for path in image_paths:
            if not os.path.isfile(path):
                print(f"    Image file not found: {path}")
                continue
                
            try:
                img = Image.open(path)
                img_width, img_height = img.size
                
                # Convert to mm - use an even larger base size for better readability
                # Use 60 DPI for much larger images
                dpi = 60
                width_mm = img_width / dpi * 25.4
                height_mm = img_height / dpi * 25.4
                
                # Set even larger minimum size for readability (at least 100mm wide)
                min_width = 100
                if width_mm < min_width:
                    scale = min_width / width_mm
                    width_mm *= scale
                    height_mm *= scale
                
                image_data.append({
                    'path': path,
                    'width_mm': width_mm,
                    'height_mm': height_mm,
                    'img': img
                })
                
                total_width += width_mm
                max_height = max(max_height, height_mm)
                
            except Exception as e:
                print(f"    Error processing image {path}: {e}")
                continue
        
        if not image_data:
            return
        
        # Calculate how many images can fit per row
        num_images = len(image_data)
        if num_images == 1:
            # Single image - use almost all of the width
            target_width = min(max_width_mm * 0.95, image_data[0]['width_mm'])
            scale = target_width / image_data[0]['width_mm']
            for data in image_data:
                data['width_mm'] *= scale
                data['height_mm'] *= scale
            max_height = image_data[0]['height_mm']
        elif num_images == 2:
            # Two images - each gets about 48% of width for maximum size
            target_width_per_image = max_width_mm * 0.48
            for data in image_data:
                if data['width_mm'] > target_width_per_image:
                    scale = target_width_per_image / data['width_mm']
                    data['width_mm'] *= scale
                    data['height_mm'] *= scale
                max_height = max(max_height, data['height_mm'])
        elif num_images <= 4:
            # 3-4 images - arrange in 2x2 grid with much larger size
            target_width_per_image = max_width_mm * 0.48
            for data in image_data:
                if data['width_mm'] > target_width_per_image:
                    scale = target_width_per_image / data['width_mm']
                    data['width_mm'] *= scale
                    data['height_mm'] *= scale
                max_height = max(max_height, data['height_mm'])
        else:
            # More than 4 images - scale down proportionally but keep large size
            if total_width > max_width_mm:
                scale = max_width_mm / total_width
                # Don't scale too small - minimum 90mm per image for readability
                min_per_image = 90
                if (max_width_mm / num_images) < min_per_image:
                    scale = min_per_image * num_images / total_width
                
                for data in image_data:
                    data['width_mm'] *= scale
                    data['height_mm'] *= scale
                max_height *= scale
        
        # Check if we need a new page
        space_needed = max_height + 5
        if self.get_y() + space_needed > self.h - self.b_margin:
            print(f"    Not enough space for {len(image_paths)} images, adding new page")
            self.add_page()
        
        # Add images in a grid layout
        current_x = self.get_x()
        current_y = self.get_y()
        
        # Calculate how many images per row - always use 2 per row
        if num_images <= 2:
            images_per_row = num_images
        else:
            images_per_row = 2  # Always 2 images per row
        
        for i, data in enumerate(image_data):
            print(f"    Adding image {i+1}/{len(image_data)} at x={current_x:.1f}, y={current_y:.1f} (size: {data['width_mm']:.1f}x{data['height_mm']:.1f}mm)")
            self.image(data['path'], x=current_x, y=current_y, w=data['width_mm'])
            
            # Move to next position
            if (i + 1) % images_per_row == 0:
                # Move to next row
                current_x = self.l_margin
                current_y += max_height + 5  # 5mm gap between rows
                
                # Check if we need a new page
                if current_y + max_height > self.h - self.b_margin:
                    self.add_page()
                    current_x = self.l_margin
                    current_y = self.get_y()
            else:
                # Move to next column
                current_x += data['width_mm'] + 5  # 5mm gap between images
        
        # Move to next line after all images
        self.ln(max_height + 5)
        print(f"    Successfully added {len(image_data)} images in grid layout")


def build_pdf(data: List[QAItem], pdf_path: str, images_dir: str) -> None:
    pdf = PDFBuilder("Free CompTIA SY0-701 Practice Questions")
    pdf.add_page()

    for global_index, item in enumerate(data, start=1):
        print(f"Processing Q{global_index} - Page {item.page_number}, Question {item.question_number_on_page}")
        print(f"  Question images: {len(item.question_images)}")
        print(f"  Explanation images: {len(item.explanation_images)}")
        
        # Question block
        pdf.add_wrapped_text(f"Q{global_index}: {item.question_text}", bold=True)

        # Question images - download first, then add side by side
        print(f"  Processing {len(item.question_images)} question images:")
        question_image_paths = []
        for idx, img_url in enumerate(item.question_images, start=1):
            print(f"    Image {idx}: {img_url}")
            local = download_image(img_url, images_dir, f"q{item.page_number}-{item.question_number_on_page}-qimg{idx}")
            if local:
                print(f"    Successfully downloaded: {local}")
                question_image_paths.append(local)
            else:
                print(f"    Failed to download: {img_url}")
        
        # Add question images side by side
        if question_image_paths:
            pdf.add_images_side_by_side(question_image_paths, images_dir)

        # Options, if present
        if item.options:
            for idx, opt in enumerate(item.options):
                letter = chr(ord("A") + idx)
                prefix = f"{letter}. "
                pdf.add_wrapped_text(prefix + opt, size=10)

        # Correct answer
        if item.correct_answer_letter:
            pdf.add_wrapped_text(f"Correct Answer: {item.correct_answer_letter}", bold=True, size=11)

        # Answer text if present (e.g., "B")
        if item.answer_text:
            pdf.add_wrapped_text(f"Answer(s): {item.answer_text}", size=10)

        # Explanation
        if item.explanation_text:
            pdf.add_wrapped_text("Explanation:", bold=True, size=10)
            pdf.add_wrapped_text(item.explanation_text, size=10)

        # Explanation images - download first, then add side by side
        print(f"  Processing {len(item.explanation_images)} explanation images:")
        explanation_image_paths = []
        for idx, img_url in enumerate(item.explanation_images, start=1):
            print(f"    Image {idx}: {img_url}")
            local = download_image(img_url, images_dir, f"q{item.page_number}-{item.question_number_on_page}-eimg{idx}")
            if local:
                print(f"    Successfully downloaded: {local}")
                explanation_image_paths.append(local)
            else:
                print(f"    Failed to download: {img_url}")
        
        # Add explanation images side by side
        if explanation_image_paths:
            pdf.add_images_side_by_side(explanation_image_paths, images_dir)

        # Separator
        pdf.ln(1)
        y = pdf.get_y()
        pdf.set_draw_color(180, 180, 180)
        pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
        pdf.ln(3)

    pdf.output(pdf_path)


def save_json(data: List[QAItem], json_path: str) -> None:
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump([asdict(x) for x in data], f, ensure_ascii=False, indent=2)


def crawl_pages(start_page: int, end_page: int, delay: float) -> List[QAItem]:
    all_items: List[QAItem] = []
    for page in tqdm(range(start_page, end_page + 1), desc="Pages", unit="page"):
        url = urljoin(BASE_URL, SECTION_PATH.format(page))
        html = fetch_html(url)
        if not html:
            continue
        items = parse_questions_from_page(html, page)
        all_items.extend(items)
        if delay > 0:
            time.sleep(delay)
    return all_items


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape FreeBrainDumps SY0-701 questions and compile a PDF")
    parser.add_argument("--start", type=int, default=1, help="Start page (inclusive)")
    parser.add_argument("--end", type=int, default=3, help="End page (inclusive)")
    parser.add_argument("--out_dir", type=str, default="out", help="Output directory for JSON/PDF")
    parser.add_argument("--images_dir", type=str, default="images", help="Directory to store downloaded images")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay in seconds between page fetches")

    args = parser.parse_args()

    ensure_dir(args.out_dir)
    ensure_dir(args.images_dir)

    items = crawl_pages(args.start, args.end, args.delay)

    # Save JSON
    json_path = os.path.join(args.out_dir, "sy0-701_questions.json")
    save_json(items, json_path)

    # Build PDF
    pdf_path = os.path.join(args.out_dir, "comptia_sy0-701_past_questions.pdf")
    build_pdf(items, pdf_path, args.images_dir)

    print(f"Saved JSON to: {json_path}")
    print(f"Saved PDF to:  {pdf_path}")


if __name__ == "__main__":
    main()
