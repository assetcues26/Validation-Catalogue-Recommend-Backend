
=== STRUCTURE / CIVIL ASSET RULES (this record looks like one) ===

**STEP 2.6: Structure assets — the asset IS the building, not what stands inside it**

Some records name a civil structure. The photo is then taken INSIDE it or up against it, so the
structure appears as the roof, walls, columns and floor AROUND the equipment — never as a
free-standing object in the middle of the frame. This is the single most common false reject.

Structure nouns: shed, store shed, storage shed, godown, warehouse, canopy, shelter, shade, room,
cabin, porta cabin, pump house, substation room, guard room, security cabin, toilet block, platform,
mezzanine, staircase, ramp, flooring, roof, partition, wall, compound wall, boundary wall, civil
work, building, block, extension, tank farm, plinth, foundation.

For these:
- The WHOLE PHOTO is the asset. APPROVE when ANY building fabric is visible: roof sheeting,
  trusses, purlins, girders, beams, columns, pillars, walls, shutters, doors, or floor slab.
- Machinery, tanks, drums, pipes, racks and stock inside are CONTENTS. They are NEVER a reason to
  reject — holding them is what the structure exists for.
- Detected Primary Asset will almost always name that equipment ("industrial storage tank",
  "pallet racking"). IGNORE it for this decision — the vision step cannot pick a building as a
  primary object, so its answer says nothing about whether the structure is present.
- Height, area or span figures in the name ("5.79 m ht", "1200 sq ft", "30 x 60") confirm a civil
  structure. Treat them as qualifiers — never try to verify the number from the photo.
- The tag normally sits on a column, pillar, wall or shutter of the structure. A tag on a painted
  column is exactly what a shed tag looks like — not evidence of a mismatch.

REJECT a structure name ONLY when no built fabric appears anywhere: open ground or sky with no
roof, wall or column, or a tight close-up of one object with no surroundings visible.

**Structure name + plant area = ONE asset, not two components.**
Indian plant registers name a structure by the SECTION it serves: "BUILDING STRAINER BLOCK",
"SHED UTILITY BLOCK", "CIVIL WORK BOILER AREA", "CANTEEN AND SERVER ROOM", "MCC ROOM",
"DG ROOM", "CONTROL ROOM UTPT PLANT", "QC LAB BUILDING". The structure noun is the HEAD; the
trailing words name the plant area it serves and are a LOCATION qualifier — NOT a second component
to hunt for.
- "BUILDING STRAINER BLOCK" → BUILDING. Do NOT look for a strainer.
- "Canteen and server room" → a building/room. Do NOT require a visible canteen counter or servers.
- "DG Room" → a room. Do NOT require a visible diesel generator.
- Do NOT apply Step 2 compound splitting when the head noun is a structure. The "and" in
  "Canteen and server room" joins two rooms of ONE building — it does not create two assets.

**STEP 2.7: Land and infrastructure assets — the tag sits on a convenient nearby post**

Names: road, internal road, pathway, footpath, paving, parking area, drainage, storm water drain,
culvert, kerb, fencing, boundary wall, gate, landscaping, garden, street lighting, yard, plinth,
land development, pipeline trench.

- You cannot fix a tag to a road surface, a drain or a lawn. Surveyors mount it on the nearest
  pole, bollard, kerb, wall or signboard. **Judge the scene, not the tag's host — the post or wall
  is only where the tag could be fixed.**
- Step 2.5 (tag placement anchor) does NOT apply here — that rule is for containers, cages and
  racks, where the tagged frame really is the asset.
- REJECT rule 6 (tag on a different object) does NOT apply to these names either: a tag on a pole,
  wall, kerb or signboard beside the road, drain or fence is on its mounting point, not on a
  separate asset.
- Judge the name against the WHOLE SCENE. If the photo shows the road, paving, drain, fence or
  yard the name refers to → APPROVE, even when Detected Primary Asset names the mounting post.
- Detected Primary Asset returning "support pole", "signboard", "bollard" or "wall" while the name
  is a road, drain or fence is EXPECTED and normal — that is the mounting point, not the asset.

**STEP 2.8: READ THE SIGNAGE — a sign naming the asset outranks everything else**

Facility photos routinely contain door plates, wall-mounted room boards, department signs, painted
equipment IDs, panel labels and bay markings. SCAN THE FRAME FOR TEXT before deciding.

- A sign naming the asset is the STRONGEST confirmation available — stronger than Detected Primary
  Asset, which only reports the largest object and cannot read intent.
- If ANY sign, board, door plate, painted marking or label in the frame matches the Asset Name or
  ANY component of it → APPROVE. The sign IS the asset's identification.
- Match signage on meaning, not wording: "EXECUTIVE LUNCH ROOM" = canteen / dining / pantry.
  "SERVER ROOM" = server room / IT room / data room. "MCC ROOM" = electrical room.
  "PUMP HOUSE" = pump room. "SUBSTATION" = substation building.
- A matching sign OVERRIDES a contradictory Detected Primary Asset. If the boards read
  "SERVER ROOM" and detectedAsset says "water treatment tank", the signage wins — the tank simply
  happens to stand in the frame.
- Photos are often rotated 90° or 180° by the capture device. Read signage in ANY orientation;
  never reject because text is sideways.
- The ONLY text to ignore is the client logo printed on the barcode sticker itself.

**STRUCTURE EXAMPLES (see Step 2.6):**
- Name: "Store Shed with 5.79 m ht", Detected: "industrial storage tank", Image: factory bay with a
  steel vessel, green piping and blue drums, roof sheeting and beams overhead, tag on a painted
  column → APPROVE (90%) — the shed IS the structure in the photo; tank and drums are its contents
- Name: "Storage Shed", Detected: "pallet racking", Image: warehouse interior, racks under a trussed
  roof → APPROVE (90%) — racking is contents; roof and columns are the shed
- Name: "RCC Shed 30x60", Detected: "forklift", Image: forklift parked under a covered bay
  → APPROVE (88%) — the covered bay is the shed; ignore the 30x60
- Name: "Pump House", Detected: "centrifugal pump", Image: small brick room housing pumps
  → APPROVE (90%) — the room is the asset, the pump is contents
- Name: "Compound Wall", Detected: "boundary wall", Image: plastered wall running along a plot
  → APPROVE (92%)
- Name: "Mezzanine Floor", Detected: "steel staircase", Image: raised steel platform with railing
  → APPROVE (88%)
- Name: "Factory Building", Detected: "packing machine", Image: production hall, machines under a
  columned roof → APPROVE (90%) — the hall is the building
- Name: "BUILDING STRAINER BLOCK", Detected: "industrial building", Image: exterior of a clad
  warehouse building → APPROVE (92%) — head noun is BUILDING and a building is right there;
  "strainer block" names the plant area served, so do NOT hunt for a strainer
- Name: "Canteen and server room", Detected: "water treatment tank", Image: a building wall carrying
  blue boards reading "SERVER ROOM" and "EXECUTIVE LUNCH ROOM", with a blue tank in the foreground
  → APPROVE (95%) — the signage names the asset outright (Step 2.8); the tank merely stands there.
  "Executive lunch room" is the canteen. Sideways text still counts
- Name: "Control Room UTPT Plant", Detected: "printer", Image: office-style room interior with desks,
  office chairs, a printer, files and windows → APPROVE (90%) — the ROOM is the asset; desks, chairs
  and printer are its contents. "UTPT Plant" names the plant area. A room does not stop being a room
  because it is furnished rather than industrial
- Name: "Road", Detected: "support pole", Image: concrete internal road with kerbs and speed
  breakers, tag on a roadside pole → APPROVE (90%) — the road fills the frame; the pole is only
  the mounting point (Step 2.7)
- Name: "Storm Water Drain", Detected: "kerb stone", Image: open drain channel running beside a road
  → APPROVE (90%)
- Name: "Compound Fencing", Detected: "gate post", Image: chain-link fence line along the boundary
  → APPROVE (90%)
- Name: "Store Shed", Detected: "laptop", Image: tight close-up of a laptop on a desk, no roof, wall
  or column anywhere in frame → REJECT (25%) — no built fabric visible at all
