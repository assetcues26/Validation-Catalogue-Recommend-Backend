=== WHAT IS IN THIS PHOTO? ===
An asset auditor photographed an asset. You are NOT told what the asset is supposed to be. Describe only what
you can see, so that someone else can check the asset register against it.

- main_object: the main physical object, named as specifically as its VISIBLE features justify, for example
  "screw air compressor package", "split air conditioner indoor unit", "electric motor on a base frame",
  "vertical cylindrical chute with bolted inspection doors". A plain casing is described as a casing; do not
  guess a specific machine that the features do not show.
- specificity: "specific" when visible features identify a particular kind of machine or item (a printed
  product name, a compressor package with its air end, a tube bundle, a forklift mast, a laboratory
  instrument); "component" when the photo shows mainly a part that many machines have (an electric motor,
  gearbox, motor-pump set, coupling, drive, control panel, nameplate, valve); "generic" when only a shape,
  casing, vessel, chute, duct, enclosure, frame or structure is visible.
- could_be: up to 4 kinds of equipment this could be or belong to, given only what is visible, most likely
  first. For a casing, vessel, chute, duct or structure, list equipment whose body looks like that. For a
  component, list machines it commonly belongs to.
- visible_parts: up to 5 parts or features you can actually see (chute, belt, motor, gearbox, bolted inspection
  door, control panel, piping, valve, wheels, fins, display).
- fixed_installed_machinery: true when the object is heavy industrial plant or machinery installed in place
  (bolted down, built into a process line or a structure, far too large to move by hand), such as feeders,
  conveyors, compressors, pump sets, chillers, crushers, mills, furnaces, transformers, cranes, tanks, ducts
  and silos. false for hand-held or bench-top items, laboratory instruments, office or building appliances (a
  split air conditioner, a water cooler, a printer), furniture, IT equipment and vehicles.
- asset_visible: true when a physical asset is in the photo; false when it shows only an asset tag or label,
  a wall, floor or bare surface, packaging, or a blank or unusable frame.

Text on an asset tag or encoded in its QR code says nothing about what the object is; ignore it.

Return JSON only, short phrases:
{"main_object": "<a few words>", "specificity": "specific", "could_be": ["<kind>"], "visible_parts": ["<part>"], "fixed_installed_machinery": true, "asset_visible": true}
