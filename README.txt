AutoCAD OFFSET + PROLONG for QGIS

Install:
1. QGIS > Plugins > Manage and Install Plugins.
2. Open the Install from ZIP tab.
3. Select this ZIP and install it.
4. Select a projected-CRS Point Layer as the active layer.
5. Click the AutoCAD OFFSET toolbar icon. Results are added to that same Point Layer.
6. Keep the required line layer visible; the plugin enables Vertex + Segment snapping on visible line layers.

Command:
1. Click FIRST POINT.
2. Click SECOND POINT. Their distance defines the baseline.
3. Enter:
   - CUTTING DISTANCE: signed distance along First -> Second.
       Positive = forward; negative = backward from First Point.
   - OFFSET DISTANCE: signed perpendicular distance.
       Positive = RIGHT; negative = LEFT.
   - PROLONG: extra forward length beyond Second Point.
   - MEASUREMENT MULTIPLIER (Into): multiplies Cutting, Offset and Prolong values.
     Default is 5. Example: Cutting 10 becomes 50; Offset -5 becomes -25; Prolong 20 becomes 100.
4. After each point is created, the next Cutting / Offset / Prolong / Into dialog opens automatically on the same baseline. Do not click First/Second Source Points again.
5. Press Cancel in the distance dialog or ESC on the map canvas to finish.

Example:
First-Second = 100 m; Prolong = 20 m.
Cutting Distance 115 m is allowed and places a point 15 m beyond Second Point.
Cutting Distance -6 m places the point 6 m behind First Point.
Offset Distance -5 m places it 5 m to the LEFT.

Output:
- New points are added directly to the currently selected Point Layer.
- Existing point-layer attributes are preserved; new feature attribute values are NULL.
- Prolong only extends the permitted cutting-distance range.
- No extra Point or Line layer is created.
The selected layer must be editable; the plugin starts editing if needed. Save edits in QGIS to keep the points permanently.

Notes:
- While OFFSET is active, the selected line layer is configured for Vertex + Segment snapping (12 pixels).
- The snap marker appears at the snapped point, including anywhere along a line edge.
- Previous QGIS snapping settings are restored when ESC ends the command.
- Use a projected CRS with linear units (metres/feet), not latitude/longitude.
- This plugin has been syntax-checked but not runtime-tested inside QGIS.

v3.1 measurement settings:
- Cutting Distance, Offset Distance, and Prolong inputs use Links, precision 0 (whole numbers).
- Measurement Multiplier (Into) defaults to 0.2 and retains decimals.
- Actual layer-coordinate distance = entered Links * 0.2 map units by default.
- Ensure your layer CRS units match your intended conversion; 0.2 is a user-defined scale, not a universal links-to-metres conversion.

v3.2 stability update:
- Keeps the two picked source points fixed; subsequent canvas clicks do not alter them.
- Handles point-creation exceptions with a clear dialog instead of an unhandled timer callback.
- Target is the currently selected Point Layer; it must be editable.
- Measurement unit label: Links; input precision: 0 decimals; multiplier default: 0.2.


Plugin icon: icon.png (used in QGIS Plugin Manager and toolbar).
