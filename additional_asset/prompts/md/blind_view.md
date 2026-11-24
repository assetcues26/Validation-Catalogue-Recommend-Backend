You are shown ONE photograph. Classify it strictly by what is physically visible in THIS frame.
You have no information about what the photo is supposed to show, and must not guess from context.

Return JSON only: {"view": "...", "objects": ["..."], "reason": "..."}

"view" must be exactly one of:
- "full_asset"    : a physical object is shown whole or substantially. Its overall shape and form are
                    visible — enough to say what KIND of object it is (a pump, a desk, a laptop, a tank).
- "partial_asset" : the frame is filled by ONE physical object's own surface or a fragment of it,
                    photographed too close to show the whole thing. You can tell a made object is there
                    — you can see where its surface ENDS at an edge or corner, or the background beyond
                    it, or fittings, joins, panels, fasteners, grain or manufactured texture on it — but
                    not the object's overall shape. A close shot of a table top, a cabinet door or an
                    equipment panel is this.
- "tag_closeup"   : the frame is dominated by an ASSET TAG — a stuck-on sticker, label or plate carrying
                    a barcode, a QR code or an asset/serial number. The object it is attached to appears
                    only as a patch of surface behind that tag.
                    This does NOT include writing applied directly to the object itself — painted,
                    stencilled, welded or engraved text such as a capacity, an equipment number, a
                    direction arrow or safety wording. That marking is part of the object, so judge the
                    object: "CAPACITY 500 LTS" painted on the side of a tank is a view of the tank.
- "packaging"     : what is shown is a product's RETAIL OR SHIPPING PACKAGING and the product itself is
                    not visible — a cardboard carton, product box or sealed shipping wrap carrying the
                    packaging's own printing: a manufacturer's product label with brand, model name,
                    serial number, part number or barcodes, shipping or courier labels, box dimensions,
                    "fragile" or "this side up" marks. The object inside the packaging cannot be seen.
                    A plain container that is itself the object in use — a storage bin, a crate, a tool
                    box, a cabinet, a carton used as a shelf — is NOT packaging: it is full_asset or
                    partial_asset. A product shown OUTSIDE or beside its box is full_asset.
- "empty"         : no physical object at all — an all-black or all-white frame, a blur in which nothing
                    can be made out, or only building fabric with no visible boundary: a plain wall,
                    floor, ceiling or sky filling the frame edge to edge. A date stamp or timestamp
                    overlay does not count as an object.

Deciding between "partial_asset" and "empty": can you see where the surface ENDS — an edge, a corner,
or a different background beyond it — or any fitting, join or manufactured detail ON it? If yes, it is
partial_asset. If the surface runs to every edge of the frame with no boundary and no detail, it is empty.

Deciding between "packaging" and "full_asset" / "partial_asset": is the surface you see the printed
packaging of some product — a product label, shipping label or carton print — with the product hidden
inside? Then it is packaging, even when an asset tag sticker is stuck on the box. If you can see the
product's own body, it is not packaging.

"objects": the physical objects you can actually see (not the sticker text), [] if none.
"reason": one short sentence naming the visual evidence.
