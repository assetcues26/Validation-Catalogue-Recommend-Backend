=== COULD THIS PHOTO HAVE BEEN TAKEN OF THE REGISTERED ASSET? ===
An auditor tagged an industrial asset and photographed it. A vision model that was NOT told what the asset
should be described the photo (below). Decide whether the customer's register record can be what was
photographed.

How industrial assets are photographed. Large machinery cannot be photographed whole: staff photograph the
part they can reach and where the tag is fixed - the motor or drive, a gearbox, a motor-pump set, a control
panel, a section of the casing, the inlet chute, a frame, a nameplate. Equipment on the same system is often
photographed in place of the asset. These are CORRECT records. Only a gross mismatch is a wrong record.

RECORD (the customer's master data)
- Asset name: {asset_name}
- Description: {description}
- Category / subcategory: {category} / {subcategory}

WHAT THE PHOTO SHOWS (written without knowing the record)
- Main object: {main_object}
- Specificity: {specificity}
- Could be: {could_be}
- Visible parts: {visible_parts}

FIRST, from the RECORD alone, say how the named asset's body usually looks in a photo (named_body):
- "vessel_or_casing": its body is a vessel, tank, casing or housing - a filter, cyclone, silo, bunker, tank,
  hopper, feeder, scrubber, dryer, reactor, furnace shell, heat exchanger shell.
- "duct_or_structure": it is installed plant seen as a duct, chute, conveyor, gallery, pipeline, cable tray,
  busbar, frame or structural section - conveyors and feeders, air slides, pot lines and their anode
  suspension, pipe racks, steel structures, civil works.
- "recognisable_machine": a self-contained machine or item with a distinctive look - an air compressor
  package, a transformer, a generator set, a pump set, a motor, a crane, a vehicle or forklift, a machine tool,
  a laboratory instrument, IT or office equipment, furniture.
- "unknown": an abbreviation, a plant code or a name you cannot place.

THE QUESTION: could someone photographing the named asset, or the part of it they can reach, plausibly have
taken this photo? Choose ONE relation:
- "same_kind": the photo shows the kind of asset the record names: a synonym, a make or model of it, or one
  that differs only in an attribute (size, rating, capacity, material, manual or motorised, model year).
- "component": the photo shows a part of the named asset (its motor, drive, gearbox, coupling, motor-pump
  set, control or starter panel, casing, housing, body section, inlet chute, frame, nameplate), or the record
  names a part, accessory or fitting of the object shown (a hose on a compressor), or one item of several the
  record lists.
- "same_system": the photographed equipment and the named asset are DIRECTLY connected parts of one process
  line or machine set, passing material, power or fluid to each other: a chiller and its cooling tower, a pump
  and its motor, an air compressor and its air receiver, a feeder and the conveyor it feeds. Being in the same
  plant, building, shop floor or industry is NOT the same system.
- "cannot_tell": the photo shows only a plain casing, vessel, chute, duct, enclosure, frame or structure, or a
  bare component, that the named asset could have (see the guidance below); or the name is an abbreviation,
  plant code or specialist term you cannot expand. An unfamiliar name is never evidence of a mismatch.
- "different_kind": NO plausible photo of the named asset or of any part of it looks like this: the photo
  plainly shows a different kind of asset. Use it only when you are sure.

Guidance from the owner of these records:
- An electric motor, gearbox, drive or motor-pump set is a "component" of any machine that has a motor: a
  pump, fan, blower, air handling unit, compressor, conveyor, crane or hoist, cooling tower, agitator, mixer,
  crusher or mill. It is "different_kind" for equipment with no motor: a transformer, a storage tank or silo,
  an electrical panel, a structure, a vehicle, a generator set's alternator.
- A weighing or coal feeder is a short enclosed belt conveyor fed through a chute: a belt conveyor record
  matches a feeder, and chutes, hoppers and skirting are components of conveyors and feeders.
- A plain vessel, chute, duct, casing or structure photographed for a "vessel_or_casing" or
  "duct_or_structure" asset is "cannot_tell" or "component", never "different_kind".
- A recognisable machine whose body is NOT a plain vessel or duct - an air compressor package, a transformer,
  a pump set, a motor, a crane, a vehicle or forklift, a machine tool, a laboratory instrument, IT or office
  equipment - does not look like a plain duct, vessel, frame or structure: a photo showing only those is
  "different_kind". A louvred metal canopy or enclosure can be the housing of a generator set or compressor:
  that is "cannot_tell".

These are "different_kind" (gross mismatches): a CNC lathe registered as a weighbridge; a steam boiler
registered as a forklift; a printing press registered as a cooling tower; an electrical distribution panel
registered as a centrifugal pump; a hydraulic press registered as a water storage tank; a laboratory
microscope registered as an air compressor; a bare structural steel frame registered as a diesel generator.

Return JSON only:
{{"named_body": "vessel_or_casing", "relation": "same_kind", "seen_kind": "<what the photo shows, a few words>", "reason": "<one sentence, max 25 words>"}}
