You are shown one or more photographs, numbered in the order given. For EACH one, decide whether it is a
RE-PHOTOGRAPH: a photo taken of another image — a picture shown on a phone, tablet, laptop or monitor
screen, or a printed photo — rather than a photo taken of a real object in front of the camera.

You have no information about what the photos are supposed to show. Judge only what is visible.

A re-photograph shows the scene as a FLAT PICTURE inside the frame. Evidence of that, and only that,
counts. Name each cue you actually see:
- "screen_bezel"      : the edge, bezel or casing of a phone, tablet, laptop or monitor framing the scene
- "device_ui"         : app or phone interface over the scene — status bar, battery or signal icons,
                        gallery or chat controls, buttons, a cursor, scroll bars
- "moire_pixel_grid"  : moiré ripples, a visible pixel grid, sub-pixel stripes or scan lines running
                        across the WHOLE scene, including across surfaces that should be smooth
- "screen_glow"       : the scene is backlit with a screen's uniform glow or colour cast, unlike the room
- "print_edge"        : the border or white margin of a printed photo, paper curl, a torn or folded edge
- "halftone_dots"     : printing dots or a paper texture across the whole scene
- "glossy_glare"      : a sheen or glare lying across the scene as if on flat glossy paper or glass held
                        in front of the camera, not on one object in it
- "held_in_hand"      : fingers or a hand holding the phone, screen or print that contains the scene
- "picture_in_scene"  : a flat rectangular picture of a scene sitting inside a different real
                        environment — on a desk, a table, a wall, a lap
- "second_timestamp"  : a date or time stamp INSIDE the pictured image, in addition to any stamp laid
                        over the whole frame. The capture app burns a date stamp into every photo, so a
                        photo of an earlier photo carries the earlier stamp inside the picture.

These are NOT re-photographs — answer false for them:
- A real screen device photographed directly as the object itself: a TV, monitor, laptop, tablet, phone,
  kiosk or display panel, even when its screen is showing a picture, a video, a desktop or text. The
  device's own body, stand, cables and surroundings are real.
- A painting, poster, framed picture, whiteboard, notice board, signboard or banner that is itself the
  object photographed, hanging or standing in a real space.
- A real object seen through glass — a display cabinet, a window, a glass door, a server rack door —
  with reflections on the glass.
- Fine repeating texture that is part of a real object: mesh, grilles, perforated metal, woven fabric,
  chain-link fencing, keyboard keys. That is not moiré across a picture.
- A date stamp laid over the whole frame by the capture app. One stamp is normal.
- Poor lighting, blur, low resolution or an odd angle on its own.

When the evidence is weak or you are unsure, answer false. A wrong "true" rejects a genuine record.

Return JSON only:
{"photos": [{"index": 1, "recaptured": true|false, "cues": ["..."], "confidence": 0.0-1.0, "reason": "..."}]}

- One entry per photo, "index" matching the order given, starting at 1.
- "cues": only names from the list above, and only ones actually visible; [] when "recaptured" is false.
- "confidence": how certain you are of "recaptured" being TRUE. Use a low value when answering false.
- "reason": one short sentence naming what you see.
