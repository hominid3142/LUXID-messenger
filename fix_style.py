
import os

def fix_css():
    file_path = 'static/style.css'
    
    # Read as binary to handle potential encoding mess
    with open(file_path, 'rb') as f:
        content = f.read()
    
    # Find the end of the valid block
    marker = b'transition: width 0.5s ease-out;\r\n}'
    # The file might use \n or \r\n
    if marker not in content:
        marker = b'transition: width 0.5s ease-out;\n}'
    
    if marker not in content:
        print("Marker not found in binary mode.")
        # Try text mode fallback if binary fails?
        # Actually, let's just find the last brace of proper CSS.
        # But assuming the file was mostly valid...
        pass

    try:
        idx = content.rfind(b'}')
        if idx != -1:
            # We want to keep everything up to the last valid '}' of the .progress-fill block
            # But there might be garbage after.
            # Let's find the specific block end.
            target_block_end = b'transition: width 0.5s ease-out;\r\n}'
            if target_block_end not in content:
                 target_block_end = b'transition: width 0.5s ease-out;\n}'
            
            cut_idx = content.find(target_block_end)
            if cut_idx != -1:
                clean_content = content[:cut_idx + len(target_block_end)]
                
                new_css = """

/* [v3.5.0] Chat View Toggle */
#main-chat {
  display: none;
  flex-direction: column;
  height: 100%;
  width: 100%;
  background: var(--bg);
  position: absolute;
  top: 0;
  left: 0;
  z-index: 100;
}

body.is-chatting #main-chat {
  display: flex !important;
}

body.is-chatting #sidebar,
body.is-chatting .bottom-nav {
  display: none !important;
}
"""
                with open(file_path, 'wb') as f:
                    f.write(clean_content + new_css.encode('utf-8'))
                print("Fixed style.css")
            else:
                print("Could not find cut point.")
        else:
            print("No braces found.")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    fix_css()
