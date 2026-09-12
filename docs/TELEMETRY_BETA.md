# Hager Local 0.2.20b2 - public telemetry beta

## English

This community integration is intended for Witty Solar linked to Flow EMC and a
myHager/Flow account. It is not an official Hager integration. Despite the project
name, its current communication is through the Hager Flow cloud, not direct
local Modbus. Internet/cloud availability is required for cloud controls.

### Included

This beta retains the existing sensors and controls from 0.2.20b1, including the
experimental whole-ampere current ceiling. It adds 22 read-only entities using
already-fetched data, with no additional endpoints or charger commands:

- Power and energy for L1, L2 and L3. L2/L3 default to disabled when not reported
  as active phases; they can be enabled in the entity settings. Units follow the
  existing integration's power and energy fields (W and Wh).
- Installer upper/lower current bounds, configured solar ratio (percent, not A),
  configured fallback current, diagnostic reference voltage and raw key code.
- A readable charging summary, derived from explicit connection/working/charging
  flags. This is not a guessed translation of undocumented numeric session codes.
- The received or remembered seven-day profile as attributes: day, target energy
  in kWh and departure time. It is not an editor and may contain cached values.
- Reported connection, working state, pending current command, RFID-reader state,
  authentication requirement, automatic phase switching, physical cable lock and
  charge cancellation.

An entity's existence does not prove that its field is meaningful on every
firmware. Missing or invalid numbers remain unknown; string values such as
"false" are not interpreted as a real false flag. A zero SoC or a bidirectional
field does not establish Tesla battery-percentage or bidirectional support; no
such misleading sensors are added. Reference voltage is explicitly diagnostic,
not presented as a verified live voltage measurement.

The existing "always lock cable" setting and the new reported physical-lock flag
are different concepts. Similarly, a configured maximum is not measured current.
The pending flag only reflects Hager's `maxChargeCurrentWatingFor` field.

### Not included

No new setters for solar ratio, minimum/fallback current, access modes or phase
management. No weekly-plan editor, session-history endpoint, WebSocket client,
local Modbus current writes or automatic cloud-to-local fallback. The current
ceiling inherited from b1 remains experimental: its actual application during a
physical charging session has not yet been validated. The beta is not a safety
controller or a guarantee against house-battery discharge into a car.

### Install and share

Share the public release page for `0.2.20b2` in `hebrru/Hager-local`, not a private
Home Assistant cache or configuration backup. The stable `0.2.19` release remains
available. For a manual installation, back up `custom_components/hager_local`,
then replace that folder with the same folder from the release source archive.
Do not copy tests or tools into the integration folder. Restart Home Assistant
to load the new modules. Existing entity IDs are retained; Home Assistant assigns
IDs for the new entities using the installation's device naming and language.

### Community diagnostics

Home Assistant's integration diagnostic export is now implemented. It returns
only allow-listed numeric/boolean readings and a reduced received/cached plan.
It does not serialize credentials, account names, tokens, addresses, device names,
serial numbers, MAC/IP addresses or full vendor payloads. This is data minimization,
not an audit or anonymity guarantee: energy readings and departure times can
still reveal household habits. Review or remove the plan before sharing publicly.
Never post `.storage`, `core.config_entries`, full caches or backups in an issue.

### Validation

The repository workflow runs offline unit tests and selected entity bindings
against synthetic dependencies, plus syntax checks. Hassfest validates Home
Assistant integration metadata. These checks are not full Home Assistant runtime
or physical charger tests. Refer to the exact release commit's Actions results.

## Francais

**0.2.20b2 ajoute 22 entites en lecture seule et un rapport de diagnostic reduit.**
Les commandes existantes sont conservees, notamment la limite de courant cloud
experimentale. Les noms des nouvelles entites sont traduits en francais dans
Home Assistant. Aucun reglage de protection ou de recharge n'est modifie par
l'installation de cette mise a jour.

Les valeurs ajoutees comprennent les puissances/energies par phase, les limites
installateur, le ratio solaire configure, le courant de secours, les diagnostics
de connexion/fonctionnement et de confirmation du courant, ainsi que le profil
hebdomadaire recu ou memorise. Ce dernier n'est pas une confirmation recente du
planning et ne permet pas encore de le modifier.

**Les autres pilotages avances, l'historique des sessions, le WebSocket et le
Modbus direct ne sont pas inclus.** Il ne faut pas annoncer ces fonctions comme
operationnelles en partageant cette version. L'application physique de la consigne
d'amperage heritee de b1 reste a verifier en recharge reelle.

Sauvegarder l'integration, copier les fichiers de la version, puis redemarrer
Home Assistant. La version stable 0.2.19 reste disponible. Pour demander de l'aide,
privilegier le rapport de diagnostic reduit et le relire avant de le publier :
les horaires et consommations peuvent encore reveler des habitudes personnelles.
