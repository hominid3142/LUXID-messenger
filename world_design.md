# LUXID World Design (Draft)

## 1. Concept
LUXID is a simplified virtual city where Eves live. It is divided into distinct "Districts" (Themes), and each District contains specific "Locations" (Spots).

-   **Scale**: Initially 5 Districts, expandable.
-   **Movement**: Eves move between locations based on their schedule and mood.
-   **Constraint**: All physical interactions (Meetups, Work, Cafe) must happen at a valid Location.

## 2. Map Hierarchy
`World` -> `District` -> `Location`

## 3. Initial Districts (The 5 Zones)

### A. **Lumina City (Central District)**
> *The beating heart of LUXID. Modern, busy, and trendy.*
-   **Vibe**: Skyscrapers, glass facades, busy streets, luxury.
-   **Key Locations**:
    1.  **Lumina Plaza**: Central meeting point, large fountain.
    2.  **The Core Tower**: Major corporate offices (Work).
    3.  **Starfield Mall**: High-end shopping and cinema.
    4.  **Beans & Bytes**: A famous franchise cafe (Work/Study).

### B. **Seren Valley (Green Zone)**
> *A place for healing and nature. Calm and fresh.*
-   **Vibe**: Trees, rivers, birds chirping, wooden architecture.
-   **Key Locations**:
    1.  **Seren Park**: Jogging tracks, picnic areas.
    2.  **Botanical Garden**: Rare plants, quiet reading spots.
    3.  **Riverside Walk**: Romantic dating course.

### C. **Echo Bay (Cultural District)**
> *Where culture, art, and the ocean meet. Hip and artistic.*
-   **Vibe**: Red brick buildings, ocean breeze, street art, indie music.
-   **Key Locations**:
    1.  **The Gallery**: Modern art exhibitions.
    2.  **Vinyl Pub**: Analog music bar.
    3.  **Seaside Deck**: Ocean view, cafes, busking.
    4.  **Blue Note Jazz Club**: Evening live performances.

### D. **The Hive (Residential Area)**
> *Where Eves live. Private and cozy.*
-   **Vibe**: Clean apartments, convenience stores, quiet streets.
-   **Key Locations**:
    1.  **Shared Apartments**: Where most Eves live (Home).
    2.  **24/7 Store**: Late night snacks.
    3.  **Community Center**: Gym and laundry.

### E. **Neon District (Nightlife)**
> *The city that never sleeps. Colorful and energetic.*
-   **Vibe**: Neon signs, loud music, energy, cyberpunk aesthetics.
-   **Key Locations**:
    1.  **Club Vertex**: Best dance floor.
    2.  **Rooftop Bar 2077**: Cocktails with a city view.
    3.  **Game Arcade**: Retro games and darts.

## 4. Technical Integration
-   **Database**:
    -   `MapLocation` table: `id`, `district`, `name`, `category`, `vibe_tags`.
    -   `Persona` table: `current_location_id` (Foreign Key).
-   **Behavior**:
    -   If `Schedule` says "Work", Eve moves to *The Core Tower*.
    -   If `Schedule` says "Rest", Eve moves to *Seren Park* or *Home*.
    -   **Feed**: When posting, the location is automatically tagged (e.g., "at Lumina Plaza").
