"""Additional TradeCraft message catalogs: fr, es, ru, ar.

Each language dict mirrors CATALOGS["en"] key-for-key and placeholder-for-placeholder.
Protocol values (tool names, build types, resource keys, project ids, action names)
stay English everywhere — only human-readable descriptions are translated.
"""

EXTRA = {
    "fr": {
        "game_over": "La partie est déjà terminée.",
        "placement_title": "Placement pour P{pid}",
        "placement_intro": "Vous êtes en phase de placement. Les règles exigent maintenant : {action}",
        "placement_need_settlement": "placez une colonie : appelez tradecraft_build avec type=\"settlement\" et un vertex_id pris dans la liste recommandée ci-dessous. Ne posez pas de route d'abord, et n'appelez pas tradecraft_build avec type=\"upgrade\" — c'est refusé pendant le placement.",
        "placement_need_road": "posez la route de la colonie que vous venez de placer : appelez tradecraft_build avec type=\"road\" et un edge_id pris dans la liste recommandée ci-dessous. Ne commencez pas une autre colonie.",
        "rec_title": "Actions recommandées pour P{pid} (légales, meilleures d'abord, max {n})",
        "rec_settlement": "colonie {vid}  ({resources})",
        "rec_road": "route {eid}  ({v1}-{v2})",
        "rec_upgrade": "amélioration {vid} → L{level}  ({cost})",
        "rec_footer": "{total} cibles de construction légales au total ; la liste complète est `expansion_targets` dans l'état.",
        "trade_pending_title": "Échange en attente",
        "trade_proposer": "Vous avez proposé un échange et attendez la décision du répondant. N'appelez aucun outil ; terminez votre réponse maintenant pour que le runner continue.",
        "trade_responder": "Vous êtes le répondant de l'échange en attente. N'appelez aucun outil pour répondre. Terminez par une décision JSON unique dans un bloc ```json avec les clés decision (accept ou reject) et reason. Une raison courte est requise. Le runner analyse ce JSON ; une décision absente ou mal formée est traitée comme un refus.",
        "invalid_player": "Joueur invalide : P{pid}.",
        "robber_needs_hex": "Déplacer le bandit exige un identifiant de tuile.",
        "invalid_hex": "Tuile invalide : {hex_id}. Utilisez un identifiant de la carte (h0-h18).",
        "robber_same_hex": "Le bandit occupe déjà {hex_id}. Choisissez une autre tuile.",
        "bank_offer_single": "Un échange bancaire ne porte que sur un seul type de ressource : offrez exactement {rate} exemplaires d'une seule ressource.",
        "bank_offer_rate": "Votre taux bancaire est {rate}:1 — offrez exactement {rate} {r}, pas {got}.",
        "bank_request_single": "Un échange bancaire ne rapporte qu'un seul type de ressource : demandez exactement 1 exemplaire.",
        "bank_request_one": "Un échange bancaire rapporte exactement 1 ressource, pas {got}.",
        "bank_same_resource": "Impossible d'échanger {r} contre {r}.",
        "dice_notice_round": "Lancers de production de cette manche : {rolls}. Chaque lancer est réglé pour toute la table : les joueurs ne gagnent donc pas les mêmes ressources. Les variations par joueur figurent dans les événements production.",
        "dice_roll_entry": "P{pid} a lancé {roll}",
        "dice_roll_entry_seven": "P{pid} a lancé 7 (aucune production ; P{pid} obtient le déplacement du bandit)",
        "dice_none_yet": "Aucun lancer de production pour l'instant dans cette manche.",
        "dice_only_in_play": "Les lancers de production n'ont lieu qu'en phase de jeu (phase={phase}).",
        "already_rolled": "P{pid} a déjà lancé cette manche.",
        "robber_notice": "Le bandit occupe {hex} ({resource}) : cette tuile ne produit rien tant qu'il y reste.",
        "robber_move_notice": "Vous détenez le déplacement du bandit pour cette manche. Terminez votre résumé de fin de tour par une ligne séparée, exactement sous cette forme :\n`ROBBER_MOVE: <id de tuile>` (par exemple `ROBBER_MOVE: h12`). N'appelez aucun outil : le runner lit cette ligne et déplace le bandit. Le bandit bloque la production de cette tuile.",
        "await_settlement": "La partie est terminée ; décompte en cours. Ne faites plus aucune action.",
        "unknown_action": "Action inconnue : {action}. Utilisez build, propose_trade, send_message, claim_project ou view_game_state.",
        "placement_only_build": "Pendant le placement, la seule action légale est build (type=settlement ou type=road). Si vous avez déjà placé tous vos villages et leurs routes, votre placement est terminé : terminez votre réponse sans appeler d'outil pour que la partie passe en phase de jeu.",
        "settlement_awaiting_road": "Vous avez déjà placé une colonie en attente de sa route. Posez d'abord la route (build type=road).",
        "place_settlement_first": "Posez d'abord la colonie, puis la route. Commencez par build type=settlement.",
        "placement_use_build": "Pendant le placement, utilisez build type=settlement ou type=road.",
        "placement_complete": "Votre placement initial est terminé. TERMINEZ VOTRE TOUR maintenant : résumez en une phrase et n'appelez AUCUN outil. La partie ne passe en phase de jeu qu'après que tous les joueurs ont terminé leur tour, et tout autre appel d'outil dans ce tour sera rejeté.",
        "not_placement_phase": "Pas en phase de placement (phase={phase}).",
        "road_needs_edge_id": "build type=road requiert un edge_id. Choisissez-en un parmi les options de route légales de l'état du jeu.",
        "invalid_edge": "Arête invalide : {edge_id}. Choisissez un edge_id parmi les options de route légales.",
        "road_exists_owned": "Une route existe déjà ici (appartenant à P{owner}). Choisissez une autre arête.",
        "max_roads": "Maximum de {n} routes atteint. Vous ne pouvez pas construire plus de routes.",
        "road_connect_settlement": "La route doit se raccorder à votre colonie fraîchement placée. Choisissez une arête adjacente.",
        "road_connect_road": "La route doit se raccorder à votre route ou bâtiment existant. Choisissez une arête adjacente.",
        "cannot_afford_road": "Impossible de payer la route. Coût : {cost}, Disponible : {have}. Échangez ou produisez d'abord les ressources manquantes.",
        "settlement_needs_vertex": "build type=settlement requiert un vertex_id. Choisissez-en un parmi les options de colonie légales.",
        "invalid_vertex": "Sommet invalide : {vertex_id}. Choisissez un vertex_id parmi les options de colonie légales.",
        "building_exists": "Un bâtiment de niveau {level} appartenant à P{pid} existe déjà ici. Choisissez un autre sommet.",
        "max_settlements": "Maximum de {n} colonies atteint. Vous ne pouvez pas placer d'autre colonie.",
        "settlement_connect_road": "La colonie doit se raccorder à l'une de vos routes. Construisez d'abord une route ou choisissez un sommet connecté.",
        "cannot_afford_settlement": "Impossible de payer la colonie. Coût : {cost}, Disponible : {have}. Échangez ou produisez d'abord les ressources manquantes.",
        "upgrade_needs_vertex": "build type=upgrade requiert un vertex_id. Choisissez l'un de vos propres bâtiments.",
        "no_building_here": "Aucun de vos bâtiments à ce sommet : {vertex_id}. Vous ne pouvez améliorer que vos propres bâtiments.",
        "upgrade_step": "Vous ne pouvez améliorer que de {cur} à {nxt}. Passez to_level={nxt}.",
        "max_level_2": "Le niveau maximal est 2 (ville). Aucune amélioration au-delà.",
        "max_cities": "Maximum de {n} villes atteint. Améliorez une autre colonie ou revendiquez un projet à la place.",
        "cannot_afford_upgrade": "Impossible de payer l'amélioration. Coût : {cost}, Disponible : {have}. Échangez ou produisez d'abord les ressources manquantes.",
        "trade_pending": "Un échange est déjà en attente. N'en proposez pas d'autre ; terminez votre réponse et laissez l'échange en attente se résoudre.",
        "insufficient_resource": "{r} insuffisant : disponible {have}, à offrir {n}. Réduisez le montant proposé.",
        "trade_target_int": "La cible de l'échange doit être un identifiant de joueur entier (0-3).",
        "invalid_trade_target": "Cible d'échange invalide : P{pid}. Choisissez un autre joueur (vous ne pouvez pas échanger avec vous-même).",
        "no_pending_trade": "Aucun échange en attente à résoudre.",
        "unknown_trade_decision": "Décision d'échange inconnue : {decision}. Utilisez accept ou reject.",
        "message_required": "Contenu du message requis. Fournissez un message de négociation non vide.",
        "unknown_project": "Projet inconnu : {pid}. Utilisez l'un de trade_master, build_master, tech_pioneer, resource_tycoon, road_king.",
        "project_claimed": "Projet déjà revendiqué par P{pid}. Revendiquez un autre projet.",
        "cannot_settle_before_play": "Impossible de décompter avant la partie (phase={phase}).",
        "settlement_pending_trade": "Le décompte nécessite d'abord que l'échange en attente soit résolu.",
        "invalid_player_id": "player_id invalide : {pid}",
        "unknown_controller_action": "Action de contrôleur inconnue : {action}",
        "invalid_registration": "Enregistrement de joueur invalide ou en double.",
        "controller_connected": "Contrôleur déjà connecté ou enregistrement invalide.",
        "internal_error": "Erreur interne du moteur : {exc}",
        "action_complete": "Votre action programmée pour ce tour est déjà terminée. Résumez brièvement ce que vous venez de faire, puis terminez votre réponse sans appeler d'outil pour rendre le contrôle au planificateur.",
        "action_near_budget": "Votre action programmée pour ce tour est déjà terminée. Vous avez utilisé la totalité des {budget} appels supplémentaires autorisés ce tour ; un appel de plus sera compté comme un forfeit. Résumez brièvement ce que vous venez de faire, puis terminez votre réponse sans appeler d'outil.",
        "action_over_budget": "Erreur : votre action programmée pour ce tour est déjà terminée et vous avez dépassé la limite de {budget} appels de ce tour ({used} appels) ; ce tour est compté comme un forfeit. Arrêtez maintenant — résumez brièvement puis terminez votre réponse sans appeler d'outil.",
        "turn_complete": "Votre action programmée pour ce tour est terminée. Résumez brièvement ce que vous venez de faire, puis terminez votre réponse sans appeler d'outil pour rendre le contrôle au planificateur.",
        "trade_quota_exhausted": "Vous avez utilisé vos {limit} propositions d'échange pour ce réveil. N'en proposez pas d'autre ; utilisez le build ou la revendication de projet ou un message privé restant, ou terminez votre réponse sans appeler d'outil.",
        "chat_quota_exhausted": "Vous avez utilisé vos {limit} messages privés pour ce réveil. N'en envoyez pas d'autre ; utilisez le build ou la revendication de projet ou une proposition d'échange restante, ou terminez votre réponse sans appeler d'outil.",
        "wake_quota_note": (
            "Ce réveil autorise une construction ou une revendication de projet (mutuellement "
            "exclusives), au plus {trade_limit} propositions d'échange et {chat_limit} messages "
            "privés. Échange et messages privés sont des étapes libres : ils ne consomment pas le "
            "créneau construction/projet et restent disponibles avant comme après lui."
        ),
        "wake_scheduled": (
            "Vous êtes maintenant programmé en tant que TradeCraft P{pid}. Lisez l'état du jeu et "
            "toute instruction d'étape, puis utilisez les outils TradeCraft ; n'expliquez pas "
            "simplement un coup. Après avoir accompli votre action programmée — une construction, "
            "une revendication de projet ou une étape de placement — "
            "arrêtez-vous immédiatement et rendez le contrôle à l'orchestrateur en terminant votre "
            "réponse sans appeler d'autre outil. Pendant la partie normale, "
            "tradecraft_send_message est disponible pour une négociation concrète, comme énoncer des "
            "besoins en ressources ou des conditions acceptables ; c'est une étape libre qui ne "
            "consomme pas votre action, vous pouvez donc écrire à plusieurs joueurs puis agir. "
            "S'il ne reste aucune action légale "
            "productive ni négociation utile, n'appelez aucun outil — terminez simplement votre "
            "réponse pour mettre fin au tour. Vous devez toujours produire une action (un appel "
            "d'outil) ou une décision explicite — une réponse vide ou silencieuse n'est jamais "
            "acceptable."
        ),
        "state_header": "État actuel de la partie",
        "resources_header": "Vos ressources (actuelles)",
        "resources_delta_header": "Changements depuis votre dernier réveil :",
        "resources_delta_none": "aucun changement",
        "events_header": "Événements depuis votre dernier tour",
        "tools_instruction": (
            "Utilisez uniquement les quatre outils `tradecraft_*` exposés par le plugin de jeu. "
            "Pour terminer un tour, n'appelez aucun outil — terminez simplement votre réponse."
        ),
        "map_topology_header": "Topologie de la carte (fixe pour cette partie ; immuable)",
        "player_identity": "Vous êtes le joueur TradeCraft P{pid}. Soyez stratégique, concis et décisif.",
        "operator_note": "L'orchestrateur de partie est l'opérateur. Suivez ses invites de tour.",
    },
    "es": {
        "game_over": "La partida ya ha terminado.",
        "placement_title": "Colocación para P{pid}",
        "placement_intro": "Estás en la fase de colocación. Ahora las reglas exigen: {action}",
        "placement_need_settlement": "coloca una colonia: llama a tradecraft_build con type=\"settlement\" y un vertex_id de la lista recomendada de abajo. No coloques una carretera primero y no llames a tradecraft_build con type=\"upgrade\": se rechaza durante la colocación.",
        "placement_need_road": "coloca la carretera de la colonia que acabas de poner: llama a tradecraft_build con type=\"road\" y un edge_id de la lista recomendada de abajo. No empieces otra colonia.",
        "rec_title": "Acciones recomendadas para P{pid} (legales, mejores primero, máx. {n})",
        "rec_settlement": "colonia {vid}  ({resources})",
        "rec_road": "carretera {eid}  ({v1}-{v2})",
        "rec_upgrade": "mejora {vid} → L{level}  ({cost})",
        "rec_footer": "{total} objetivos de construcción legales en total; la lista completa está en `expansion_targets` del estado.",
        "trade_pending_title": "Intercambio pendiente",
        "trade_proposer": "Propusiste un intercambio y esperas la decisión del respondedor. No llames a ninguna herramienta; termina tu respuesta ahora para que el runner continúe.",
        "trade_responder": "Eres el respondedor del intercambio pendiente. No llames a ninguna herramienta para responder. Termina con una única decisión JSON en un bloque ```json con las claves decision (accept o reject) y reason. Se requiere una razón breve. El runner analiza este JSON; una decisión ausente o mal formada se trata como rechazo.",
        "invalid_player": "Jugador no válido: P{pid}.",
        "robber_needs_hex": "Mover al ladrón requiere un id de hexágono.",
        "invalid_hex": "Hexágono no válido: {hex_id}. Usa un id del mapa (h0-h18).",
        "robber_same_hex": "El ladrón ya está en {hex_id}. Elige otro hexágono.",
        "bank_offer_single": "El comercio bancario acepta un solo tipo de recurso: ofrece exactamente {rate} de un mismo recurso.",
        "bank_offer_rate": "Tu tasa bancaria es {rate}:1 — ofrece exactamente {rate} de {r}, no {got}.",
        "bank_request_single": "El comercio bancario entrega un solo tipo de recurso: pide exactamente 1.",
        "bank_request_one": "El comercio bancario entrega exactamente 1 recurso, no {got}.",
        "bank_same_resource": "No puedes cambiar {r} por {r}.",
        "dice_notice_round": "Tiradas de producción de esta ronda: {rolls}. Cada tirada se resuelve para toda la mesa, así que los jugadores no ganan los mismos recursos; los cambios por jugador están en los eventos production.",
        "dice_roll_entry": "P{pid} sacó {roll}",
        "dice_roll_entry_seven": "P{pid} sacó 7 (sin producción; P{pid} obtiene el movimiento del bandido)",
        "dice_none_yet": "Todavía no ha habido tiradas de producción en esta ronda.",
        "dice_only_in_play": "Las tiradas de producción solo ocurren en la fase de juego (fase={phase}).",
        "already_rolled": "P{pid} ya tiró en esta ronda.",
        "robber_notice": "El ladrón ocupa {hex} ({resource}): ese hexágono no produce nada mientras siga ahí.",
        "robber_move_notice": "Tienes el movimiento del ladrón de esta ronda. Termina tu resumen de fin de turno con una línea separada, exactamente con esta forma:\n`ROBBER_MOVE: <id de hexágono>` (por ejemplo `ROBBER_MOVE: h12`). No llames a ninguna herramienta: el runner lee esa línea y mueve al ladrón. El ladrón bloquea la producción de ese hexágono.",
        "await_settlement": "La partida ha terminado; en espera del recuento. No actúes más.",
        "unknown_action": "Acción desconocida: {action}. Usa build, propose_trade, send_message, claim_project o view_game_state.",
        "placement_only_build": "Durante la colocación, la única acción legal es build (type=settlement o type=road). Si ya has colocado todos tus poblados y sus caminos, tu colocación ha terminado: termina tu respuesta sin llamar a ninguna herramienta para que la partida pase a la fase de juego.",
        "settlement_awaiting_road": "Ya colocaste un poblado a la espera de su carretera. Coloca primero la carretera (build type=road).",
        "place_settlement_first": "Coloca primero el poblado y luego la carretera. Empieza con build type=settlement.",
        "placement_use_build": "Durante la colocación, usa build type=settlement o type=road.",
        "placement_complete": "Tu colocación inicial está completa. TERMINA TU TURNO ahora: resume en una frase y NO llames a ninguna herramienta. La partida pasa a la fase de juego solo cuando todos los jugadores han terminado su turno, y cualquier otra llamada en este turno se rechazará.",
        "not_placement_phase": "No estás en la fase de colocación (phase={phase}).",
        "road_needs_edge_id": "build type=road requiere un edge_id. Elige uno entre las opciones legales de carretera del estado de la partida.",
        "invalid_edge": "Arista no válida: {edge_id}. Elige un edge_id entre las opciones legales de carretera.",
        "road_exists_owned": "Aquí ya existe una carretera (de P{owner}). Elige otra arista.",
        "max_roads": "Alcanzaste el máximo de {n} carreteras. No puedes construir más.",
        "road_connect_settlement": "La carretera debe conectar con tu poblado recién colocado. Elige una arista adyacente.",
        "road_connect_road": "La carretera debe conectar con tu carretera o edificio existente. Elige una arista adyacente.",
        "cannot_afford_road": "No puedes costear la carretera. Coste: {cost}, Tienes: {have}. Intercambia o produce antes los recursos que faltan.",
        "settlement_needs_vertex": "build type=settlement requiere un vertex_id. Elige uno entre las opciones legales de poblado.",
        "invalid_vertex": "Vértice no válido: {vertex_id}. Elige un vertex_id entre las opciones legales de poblado.",
        "building_exists": "Aquí ya existe un edificio de nivel {level} de P{pid}. Elige otro vértice.",
        "max_settlements": "Alcanzaste el máximo de {n} poblados. No puedes colocar otro.",
        "settlement_connect_road": "El poblado debe conectar con una de tus carreteras. Construye primero una carretera o elige un vértice conectado.",
        "cannot_afford_settlement": "No puedes costear el poblado. Coste: {cost}, Tienes: {have}. Intercambia o produce antes los recursos que faltan.",
        "upgrade_needs_vertex": "build type=upgrade requiere un vertex_id. Elige uno de tus propios edificios.",
        "no_building_here": "No hay un edificio tuyo en este vértice: {vertex_id}. Solo puedes mejorar tus propios edificios.",
        "upgrade_step": "Solo puedes mejorar de {cur} a {nxt}. Pasa to_level={nxt}.",
        "max_level_2": "El nivel máximo es 2 (ciudad). No se puede mejorar más.",
        "max_cities": "Alcanzaste el máximo de {n} ciudades. Mejora otro poblado o reclama un proyecto en su lugar.",
        "cannot_afford_upgrade": "No puedes costear la mejora. Coste: {cost}, Tienes: {have}. Intercambia o produce antes los recursos que faltan.",
        "trade_pending": "Ya hay un comercio pendiente. No propongas otro; termina tu respuesta y deja que se resuelva el comercio pendiente.",
        "insufficient_resource": "{r} insuficiente: tienes {have}, ofreces {n}. Reduce la cantidad ofrecida.",
        "trade_target_int": "El objetivo del comercio debe ser un id de jugador entero (0-3).",
        "invalid_trade_target": "Objetivo de comercio no válido: P{pid}. Elige otro jugador (no puedes comerciar contigo mismo).",
        "no_pending_trade": "No hay ningún comercio pendiente que resolver.",
        "unknown_trade_decision": "Decisión de comercio desconocida: {decision}. Usa accept o reject.",
        "message_required": "Se requiere el contenido del mensaje. Proporciona un mensaje de negociación no vacío.",
        "unknown_project": "Proyecto desconocido: {pid}. Usa uno de trade_master, build_master, tech_pioneer, resource_tycoon, road_king.",
        "project_claimed": "Proyecto ya reclamado por P{pid}. Reclama otro proyecto.",
        "cannot_settle_before_play": "No se puede hacer el recuento antes de la partida (phase={phase}).",
        "settlement_pending_trade": "El recuento requiere resolver primero el comercio pendiente.",
        "invalid_player_id": "player_id no válido: {pid}",
        "unknown_controller_action": "Acción de controlador desconocida: {action}",
        "invalid_registration": "Registro de jugador no válido o duplicado.",
        "controller_connected": "El controlador ya está conectado o el registro no es válido.",
        "internal_error": "Error interno del motor: {exc}",
        "action_complete": "Tu acción programada de este turno ya está completa. Haz un breve resumen de lo que acabas de hacer y luego termina tu respuesta sin llamar a ninguna herramienta para devolver el control al planificador.",
        "action_near_budget": "Tu acción programada de este turno ya está completa. Has usado todos los {budget} llamados adicionales permitidos este turno; un llamado más se contará como un forfeit. Haz un breve resumen de lo que acabas de hacer y luego termina tu respuesta sin llamar a ninguna herramienta.",
        "action_over_budget": "Error: tu acción programada de este turno ya está completa y has superado el límite de {budget} llamados de este turno ({used} llamados); este turno se cuenta como un forfeit. Detente ahora — haz un breve resumen y termina tu respuesta sin llamar a ninguna herramienta.",
        "turn_complete": "Tu acción programada de este turno ya está terminada. Haz un breve resumen de lo que acabas de hacer y luego termina tu respuesta sin llamar a ninguna herramienta para devolver el control al planificador.",
        "trade_quota_exhausted": "Has usado tus {limit} propuestas de intercambio de este despertar. No propongas otra; usa el build o la reclamación de proyecto o un mensaje privado que te quede, o termina tu respuesta sin llamar a ninguna herramienta.",
        "chat_quota_exhausted": "Has usado tus {limit} mensajes privados de este despertar. No envíes otro; usa el build o la reclamación de proyecto o una propuesta de intercambio que te quede, o termina tu respuesta sin llamar a ninguna herramienta.",
        "wake_quota_note": (
            "Este despertar permite una construcción o una reclamación de proyecto (mutuamente "
            "excluyentes), como máximo {trade_limit} propuestas de intercambio y {chat_limit} "
            "mensajes privados. El intercambio y los mensajes privados son pasos libres: no "
            "consumen el cupo de construcción/proyecto y siguen disponibles antes y después de él."
        ),
        "wake_scheduled": (
            "Ahora estás programado como TradeCraft P{pid}. Lee el estado de la partida y cualquier "
            "instrucción de paso, y luego usa las herramientas de TradeCraft; no te limites a "
            "explicar una jugada. Después de completar tu acción programada — una construcción, una "
            "reclamación de proyecto o un paso de colocación — detente de "
            "inmediato y devuelve el control al árbitro terminando tu respuesta sin llamar a ninguna "
            "otra herramienta. Durante la partida normal, tradecraft_send_message está disponible "
            "para una negociación concreta, como indicar necesidades de recursos o condiciones "
            "aceptables; es un paso libre que no consume tu acción, así que puedes escribir a varios "
            "jugadores y luego actuar. Si no queda ninguna acción "
            "legal productiva ni negociación que valga la pena, no llames a ninguna herramienta — "
            "termina simplemente tu respuesta para finalizar el turno. Siempre debes producir una "
            "acción (una llamada a herramienta) o una decisión explícita — una respuesta vacía o "
            "silenciosa nunca es aceptable."
        ),
        "state_header": "Estado actual de la partida",
        "resources_header": "Tus recursos (actuales)",
        "resources_delta_header": "Cambios desde tu último turno:",
        "resources_delta_none": "sin cambios",
        "events_header": "Eventos desde tu último turno",
        "tools_instruction": (
            "Usa solo las cuatro herramientas `tradecraft_*` expuestas por el plugin del juego. "
            "Para terminar un turno, no llames a ninguna herramienta — termina simplemente tu "
            "respuesta."
        ),
        "map_topology_header": "Topología del mapa (fija para esta partida; inmutable)",
        "player_identity": "Eres el jugador P{pid} de TradeCraft. Sé estratégico, conciso y decidido.",
        "operator_note": "El árbitro de la partida es el operador. Sigue sus indicaciones de turno.",
    },
    "ru": {
        "game_over": "Игра уже завершена.",
        "placement_title": "Расстановка для P{pid}",
        "placement_intro": "Вы в фазе расстановки. Сейчас правила требуют: {action}",
        "placement_need_settlement": "поставьте поселение: вызовите tradecraft_build с type=\"settlement\" и vertex_id из списка рекомендаций ниже. Не ставьте сначала дорогу и не вызывайте tradecraft_build с type=\"upgrade\" — в расстановке это отклоняется.",
        "placement_need_road": "поставьте дорогу к только что поставленному поселению: вызовите tradecraft_build с type=\"road\" и edge_id из списка рекомендаций ниже. Не начинайте новое поселение.",
        "rec_title": "Рекомендуемые действия для P{pid} (легальные, лучшие первыми, не более {n})",
        "rec_settlement": "поселение {vid}  ({resources})",
        "rec_road": "дорога {eid}  ({v1}-{v2})",
        "rec_upgrade": "улучшение {vid} → L{level}  ({cost})",
        "rec_footer": "всего {total} легальных целей строительства; полный список — `expansion_targets` в состоянии.",
        "trade_pending_title": "Сделка ожидает ответа",
        "trade_proposer": "Вы предложили сделку и ждёте решения отвечающей стороны. Не вызывайте инструментов; завершите ответ сейчас, чтобы runner продолжил.",
        "trade_responder": "Вы отвечающая сторона по ожидающей сделке. Не вызывайте никаких инструментов для ответа. Завершите одним JSON-решением в блоке ```json с ключами decision (accept или reject) и reason. Краткая причина обязательна. Runner разбирает этот JSON; отсутствующее или некорректное решение считается отказом.",
        "invalid_player": "Недопустимый игрок: P{pid}.",
        "robber_needs_hex": "Для перемещения разбойника нужен id гекса.",
        "invalid_hex": "Неверный гекс: {hex_id}. Используйте id с карты (h0-h18).",
        "robber_same_hex": "Разбойник уже на {hex_id}. Выберите другой гекс.",
        "bank_offer_single": "Банковский обмен принимает только один тип ресурса: предложите ровно {rate} одного ресурса.",
        "bank_offer_rate": "Ваш банковский курс {rate}:1 — предложите ровно {rate} {r}, а не {got}.",
        "bank_request_single": "Банковский обмен даёт только один тип ресурса: запросите ровно 1.",
        "bank_request_one": "Банковский обмен даёт ровно 1 ресурс, а не {got}.",
        "bank_same_resource": "Нельзя обменять {r} на {r}.",
        "dice_notice_round": "Броски производства в этом раунде: {rolls}. Каждый бросок рассчитывается для всего стола, поэтому игроки получают разные ресурсы; изменения по игрокам — в событиях production.",
        "dice_roll_entry": "P{pid} выбросил {roll}",
        "dice_roll_entry_seven": "P{pid} выбросил 7 (производства нет; ход разбойника у P{pid})",
        "dice_none_yet": "В этом раунде ещё не было бросков производства.",
        "dice_only_in_play": "Броски производства происходят только в фазе игры (фаза={phase}).",
        "already_rolled": "P{pid} уже бросал в этом раунде.",
        "robber_notice": "Разбойник на {hex} ({resource}): этот гекс не производит ничего, пока разбойник там.",
        "robber_move_notice": "Право хода разбойником в этом раунде у вас. Завершите итоговый текст конца хода отдельной строкой строго в таком виде:\n`ROBBER_MOVE: <id гекса>` (например, `ROBBER_MOVE: h12`). Не вызывайте инструмент: runner читает эту строку и двигает разбойника. Разбойник блокирует производство на этом гексе.",
        "await_settlement": "Игра завершена; идёт подсчёт. Больше ничего не предпринимайте.",
        "unknown_action": "Неизвестное действие: {action}. Используйте build, propose_trade, send_message, claim_project или view_game_state.",
        "placement_only_build": "На этапе размещения единственное законное действие — build (type=settlement или type=road). Если вы уже разместили все свои поселения и дороги, размещение завершено: закончите ответ, не вызывая инструменты, чтобы игра перешла в основную фазу.",
        "settlement_awaiting_road": "Вы уже разместили поселение и оно ждёт дороги. Сначала постройте дорогу (build type=road).",
        "place_settlement_first": "Сначала разместите поселение, затем дорогу. Начните с build type=settlement.",
        "placement_use_build": "На этапе размещения используйте build type=settlement или type=road.",
        "placement_complete": "Ваша начальная расстановка завершена. ЗАВЕРШИТЕ ХОД сейчас: коротко подытожьте и НЕ вызывайте инструменты. Игра переходит в основную фазу только после того, как все игроки завершат свой ход, а любые дальнейшие вызовы в этом ходу будут отклонены.",
        "not_placement_phase": "Сейчас не этап размещения (phase={phase}).",
        "road_needs_edge_id": "build type=road требует edge_id. Выберите его из законных вариантов дорог в состоянии игры.",
        "invalid_edge": "Недопустимое ребро: {edge_id}. Выберите edge_id из законных вариантов дорог.",
        "road_exists_owned": "Здесь уже есть дорога (принадлежит P{owner}). Выберите другое ребро.",
        "max_roads": "Достигнут максимум в {n} дорог. Вы не можете строить больше дорог.",
        "road_connect_settlement": "Дорога должна соединяться с вашим только что размещённым поселением. Выберите соседнее ребро.",
        "road_connect_road": "Дорога должна соединяться с вашей существующей дорогой или постройкой. Выберите соседнее ребро.",
        "cannot_afford_road": "Не хватает на дорогу. Нужно: {cost}, Есть: {have}. Сначала обменяйте или произведите недостающие ресурсы.",
        "settlement_needs_vertex": "build type=settlement требует vertex_id. Выберите его из законных вариантов поселений.",
        "invalid_vertex": "Недопустимая вершина: {vertex_id}. Выберите vertex_id из законных вариантов поселений.",
        "building_exists": "Здесь уже есть постройка уровня {level} игрока P{pid}. Выберите другую вершину.",
        "max_settlements": "Достигнут максимум в {n} поселений. Вы не можете разместить ещё одно.",
        "settlement_connect_road": "Поселение должно соединяться с одной из ваших дорог. Сначала постройте дорогу или выберите соединённую вершину.",
        "cannot_afford_settlement": "Не хватает на поселение. Нужно: {cost}, Есть: {have}. Сначала обменяйте или произведите недостающие ресурсы.",
        "upgrade_needs_vertex": "build type=upgrade требует vertex_id. Выберите одну из своих построек.",
        "no_building_here": "В этой вершине нет вашей постройки: {vertex_id}. Улучшать можно только свои постройки.",
        "upgrade_step": "Улучшать можно только с {cur} на {nxt}. Передайте to_level={nxt}.",
        "max_level_2": "Максимальный уровень — 2 (город). Дальше улучшать нельзя.",
        "max_cities": "Достигнут максимум в {n} городов. Улучшите другое поселение или вместо этого получите проект.",
        "cannot_afford_upgrade": "Не хватает на улучшение. Нужно: {cost}, Есть: {have}. Сначала обменяйте или произведите недостающие ресурсы.",
        "trade_pending": "Уже есть ожидающая сделка. Не предлагайте другую; завершите ответ и дайте ожидающей сделке разрешиться.",
        "insufficient_resource": "{r} не хватает: есть {have}, предлагается {n}. Уменьшите предлагаемое количество.",
        "trade_target_int": "Цель сделки должна быть целочисленным id игрока (0-3).",
        "invalid_trade_target": "Недопустимая цель сделки: P{pid}. Выберите другого игрока (нельзя торговать с самим собой).",
        "no_pending_trade": "Нет ожидающих сделок для разрешения.",
        "unknown_trade_decision": "Неизвестное решение по сделке: {decision}. Используйте accept или reject.",
        "message_required": "Требуется содержимое сообщения. Укажите непустое сообщение переговоров.",
        "unknown_project": "Неизвестный проект: {pid}. Используйте один из trade_master, build_master, tech_pioneer, resource_tycoon, road_king.",
        "project_claimed": "Проект уже получен игроком P{pid}. Получите другой проект.",
        "cannot_settle_before_play": "Нельзя делать подсчёт до игры (phase={phase}).",
        "settlement_pending_trade": "Для подсчёта сначала нужно разрешить ожидающую сделку.",
        "invalid_player_id": "Недопустимый player_id: {pid}",
        "unknown_controller_action": "Неизвестное действие контроллера: {action}",
        "invalid_registration": "Регистрация игрока недопустима или повторяется.",
        "controller_connected": "Контроллер уже подключён или регистрация недопустима.",
        "internal_error": "Внутренняя ошибка движка: {exc}",
        "action_complete": "Ваше запланированное действие на этот ход уже выполнено. Кратко подытожьте, что вы только что сделали, затем завершите ответ, не вызывая инструменты, чтобы вернуть управление планировщику.",
        "action_near_budget": "Ваше запланированное действие на этот ход уже выполнено. Вы израсходовали все {budget} дополнительных вызовов, разрешённых на этот ход; ещё один вызов будет засчитан как forfeit. Кратко подытожьте, что вы только что сделали, затем завершите ответ, не вызывая инструменты.",
        "action_over_budget": "Ошибка: ваше запланированное действие на этот ход уже выполнено, и вы превысили лимит в {budget} вызовов на этот ход ({used} вызовов); этот ход засчитывается как forfeit. Остановитесь — кратко подытожьте и завершите ответ, не вызывая инструменты.",
        "turn_complete": "Ваше запланированное действие на этот ход выполнено. Кратко подытожьте, что вы только что сделали, затем завершите ответ, не вызывая инструменты, чтобы вернуть управление планировщику.",
        "trade_quota_exhausted": "Вы израсходовали все {limit} предложения сделки за это пробуждение. Не предлагайте ещё; используйте оставшиеся build, получение проекта или личное сообщение либо завершите ответ, не вызывая инструментов.",
        "chat_quota_exhausted": "Вы израсходовали все {limit} личных сообщения за это пробуждение. Не отправляйте ещё; используйте оставшиеся build, получение проекта или предложение сделки либо завершите ответ, не вызывая инструментов.",
        "wake_quota_note": (
            "За это пробуждение разрешены одна постройка или одна заявка проекта "
            "(взаимоисключающие), не более {trade_limit} предложений сделки и {chat_limit} "
            "личных сообщений. Сделка и личные сообщения — бесплатные шаги: они не расходуют "
            "слот постройки/проекта и доступны как до него, так и после."
        ),
        "wake_scheduled": (
            "Сейчас вы запланированы как TradeCraft P{pid}. Прочитайте состояние игры и любую "
            "пошаговую инструкцию, затем используйте инструменты TradeCraft; не просто объясняйте "
            "ход. После выполнения запланированного действия — успешного строительства, "
            "получения проекта или шага размещения — немедленно остановитесь и "
            "верните управление ведущему, завершив ответ без дальнейших вызовов инструментов. "
            "В ходе обычной игры доступен tradecraft_send_message для конкретных переговоров, "
            "например для указания потребностей в ресурсах или приемлемых условий; это "
            "бесплатный шаг, не расходующий действие, поэтому можно написать нескольким игрокам и "
            "затем действовать. Если не осталось продуктивного законного действия или стоящих переговоров, "
            "не вызывайте инструменты — просто завершите ответ, чтобы закончить ход. Вы всегда "
            "должны произвести действие (вызов инструмента) или явное решение — пустой или тихий "
            "ответ никогда не допустим."
        ),
        "state_header": "Текущее состояние игры",
        "resources_header": "Ваши ресурсы (сейчас)",
        "resources_delta_header": "Изменения с вашего прошлого пробуждения:",
        "resources_delta_none": "без изменений",
        "events_header": "События с вашего последнего хода",
        "tools_instruction": (
            "Используйте только четыре инструмента `tradecraft_*`, предоставленные игровым "
            "плагином. Чтобы завершить ход, не вызывайте инструменты — просто завершите ответ."
        ),
        "map_topology_header": "Топология карты (фиксирована для этой партии; неизменна)",
        "player_identity": "Вы — игрок TradeCraft P{pid}. Действуйте стратегично, кратко и решительно.",
        "operator_note": "Ведущий партии — это оператор. Следуйте его подсказкам хода.",
    },
    "ar": {
        "game_over": "اللعبة انتهت بالفعل.",
        "placement_title": "التوزيع للاعب P{pid}",
        "placement_intro": "أنت في مرحلة التوزيع. تطلب القواعد الآن: {action}",
        "placement_need_settlement": "ضع مستعمرة الآن: استدعِ tradecraft_build مع type=\"settlement\" وvertex_id من قائمة التوصيات أدناه. لا تضع طريقًا أولًا، ولا تستدعِ tradecraft_build مع type=\"upgrade\" — يُرفض ذلك في مرحلة التوزيع.",
        "placement_need_road": "ضع طريق المستعمرة التي وضعتها للتو: استدعِ tradecraft_build مع type=\"road\" وedge_id من قائمة التوصيات أدناه. لا تبدأ مستعمرة أخرى.",
        "rec_title": "الحركات الموصى بها للاعب P{pid} (قانونية، الأفضل أولًا، بحد أقصى {n})",
        "rec_settlement": "مستعمرة {vid}  ({resources})",
        "rec_road": "طريق {eid}  ({v1}-{v2})",
        "rec_upgrade": "ترقية {vid} → L{level}  ({cost})",
        "rec_footer": "إجمالي {total} هدف بناء قانوني؛ القائمة الكاملة في `expansion_targets` ضمن الحالة.",
        "trade_pending_title": "صفقة معلّقة",
        "trade_proposer": "لقد اقترحت صفقة وتنتظر قرار الطرف المستجيب. لا تستدعِ أي أداة؛ أنهِ ردك الآن ليواصل runner.",
        "trade_responder": "أنت الطرف المستجيب للصفقة المعلّقة. لا تستدعِ أي أداة للرد. أنهِ بقرار JSON واحد داخل كتلة ```json بالمفتاحين decision (accept أو reject) و reason. سبب قصير مطلوب. يحلّل runner هذا JSON؛ القرار الغائب أو غير الصحيح يُعامل كرفض.",
        "invalid_player": "لاعب غير صالح: P{pid}.",
        "robber_needs_hex": "تحريك اللص يتطلب معرّف قطعة أرض.",
        "invalid_hex": "قطعة أرض غير صالحة: {hex_id}. استخدم معرّفاً من الخريطة (h0-h18).",
        "robber_same_hex": "اللص موجود بالفعل على {hex_id}. اختر قطعة أخرى.",
        "bank_offer_single": "التبادل المصرفي يقبل نوعاً واحداً من الموارد: قدّم بالضبط {rate} من مورد واحد.",
        "bank_offer_rate": "سعرك المصرفي {rate}:1 — قدّم بالضبط {rate} من {r} وليس {got}.",
        "bank_request_single": "التبادل المصرفي يمنح نوعاً واحداً من الموارد: اطلب بالضبط 1.",
        "bank_request_one": "التبادل المصرفي يمنح مورداً واحداً بالضبط، وليس {got}.",
        "bank_same_resource": "لا يمكن تبادل {r} بـ {r}.",
        "dice_notice_round": "رميات الإنتاج في هذه الجولة: {rolls}. كل رمية تُسوَّى للطاولة بأكملها، لذا لا يحصل اللاعبون على الموارد نفسها؛ وتغييرات كل لاعب في أحداث production.",
        "dice_roll_entry": "P{pid} رمى {roll}",
        "dice_roll_entry_seven": "P{pid} رمى 7 (لا إنتاج؛ وحركة السارق لـ P{pid})",
        "dice_none_yet": "لم تحدث أي رمية إنتاج في هذه الجولة بعد.",
        "dice_only_in_play": "رميات الإنتاج تحدث فقط في مرحلة اللعب (المرحلة={phase}).",
        "already_rolled": "P{pid} رمى بالفعل في هذه الجولة.",
        "robber_notice": "اللص على {hex} ({resource}): هذه القطعة لا تنتج شيئاً ما دام اللص عليها.",
        "robber_move_notice": "حق تحريك اللص في هذه الجولة لك. أنهِ ملخص نهاية دورك بسطر منفصل بهذا الشكل بالضبط:\n`ROBBER_MOVE: <معرّف القطعة>` (مثل `ROBBER_MOVE: h12`). لا تستدعِ أداة: يقرأ المشغّل هذا السطر ويحرّك اللص. اللص يمنع الإنتاج في تلك القطعة.",
        "await_settlement": "اللعبة انتهت؛ بانتظار الحساب الختامي. لا تتخذ أي إجراء آخر.",
        "unknown_action": "إجراء غير معروف: {action}. استخدم build أو propose_trade أو send_message أو claim_project أو view_game_state.",
        "placement_only_build": "أثناء التموضع، الإجراء القانوني الوحيد هو build (type=settlement أو type=road). إذا كنت قد وضعت كل مستوطناتك وطرقها، فقد انتهى تموضعك: أنهِ ردّك دون استدعاء أي أداة لتنتقل اللعبة إلى مرحلة اللعب.",
        "settlement_awaiting_road": "لقد وضعتَ مستوطنةً بانتظار طريقها. ضع الطريق أولًا (build type=road).",
        "place_settlement_first": "ضع المستوطنة أولًا ثم الطريق. ابدأ بـ build type=settlement.",
        "placement_use_build": "أثناء التموضع استخدم build type=settlement أو type=road.",
        "placement_complete": "اكتمل توزيعك الابتدائي. أنهِ دورك الآن: لخّص في سطر واحد ولا تستدعِ أي أداة. لا تنتقل اللعبة إلى مرحلة اللعب إلا بعد أن ينهي جميع اللاعبين أدوارهم، وأي استدعاء آخر في هذا الدور سيُرفض.",
        "not_placement_phase": "لست في مرحلة التموضع (phase={phase}).",
        "road_needs_edge_id": "يتطلب build type=road معرّف edge_id. اختر واحدًا من خيارات الطرق القانونية في حالة اللعبة.",
        "invalid_edge": "ضلع غير صالح: {edge_id}. اختر edge_id من خيارات الطرق القانونية.",
        "road_exists_owned": "يوجد طريق هنا بالفعل (مملوك لـ P{owner}). اختر ضلعًا آخر.",
        "max_roads": "بلغتَ الحد الأقصى وهو {n} طرق. لا يمكنك بناء المزيد.",
        "road_connect_settlement": "يجب أن يتصل الطريق بمستوطنتك الموضوعة حديثًا. اختر ضلعًا مجاورًا.",
        "road_connect_road": "يجب أن يتصل الطريق بطريقك أو مبناك القائم. اختر ضلعًا مجاورًا.",
        "cannot_afford_road": "لا يمكنك تحمّل تكلفة الطريق. التكلفة: {cost}، لديك: {have}. قايض أو أنتج الموارد الناقصة أولًا.",
        "settlement_needs_vertex": "يتطلب build type=settlement معرّف vertex_id. اختر واحدًا من خيارات المستوطنات القانونية.",
        "invalid_vertex": "رأس غير صالح: {vertex_id}. اختر vertex_id من خيارات المستوطنات القانونية.",
        "building_exists": "يوجد هنا بالفعل مبنى من المستوى {level} مملوك لـ P{pid}. اختر رأسًا آخر.",
        "max_settlements": "بلغتَ الحد الأقصى وهو {n} مستوطنة. لا يمكنك وضع المزيد.",
        "settlement_connect_road": "يجب أن تتصل المستوطنة بأحد طرقك. ابنِ طريقًا أولًا أو اختر رأسًا متصلًا.",
        "cannot_afford_settlement": "لا يمكنك تحمّل تكلفة المستوطنة. التكلفة: {cost}، لديك: {have}. قايض أو أنتج الموارد الناقصة أولًا.",
        "upgrade_needs_vertex": "يتطلب build type=upgrade معرّف vertex_id. اختر أحد مبانيك أنت.",
        "no_building_here": "لا يوجد مبنًى لك في هذا الرأس: {vertex_id}. يمكنك ترقية مبانيك أنت فقط.",
        "upgrade_step": "يمكنك الترقية من {cur} إلى {nxt} فقط. مرّر to_level={nxt}.",
        "max_level_2": "المستوى الأقصى هو 2 (مدينة). لا يمكن الترقية أكثر.",
        "max_cities": "بلغتَ الحد الأقصى وهو {n} مدينة. رقِّ مستوطنةً أخرى أو طالِب بمشروع بدلًا من ذلك.",
        "cannot_afford_upgrade": "لا يمكنك تحمّل تكلفة الترقية. التكلفة: {cost}، لديك: {have}. قايض أو أنتج الموارد الناقصة أولًا.",
        "trade_pending": "هناك صفقة معلّقة بالفعل. لا تعرض أخرى؛ اختم ردّك ودَع الصفقة المعلّقة تُحل.",
        "insufficient_resource": "{r} غير كافٍ: لديك {have}، وتعرض {n}. خفّض الكمية المعروضة.",
        "trade_target_int": "يجب أن يكون هدف الصفقة معرّف لاعب صحيحًا (0-3).",
        "invalid_trade_target": "هدف صفقة غير صالح: P{pid}. اختر لاعبًا آخر (لا يمكنك المقايضة مع نفسك).",
        "no_pending_trade": "لا توجد صفقة معلّقة لحلّها.",
        "unknown_trade_decision": "قرار صفقة غير معروف: {decision}. استخدم accept أو reject.",
        "message_required": "محتوى الرسالة مطلوب. قدّم رسالة تفاوض غير فارغة.",
        "unknown_project": "مشروع غير معروف: {pid}. استخدم أحد trade_master أو build_master أو tech_pioneer أو resource_tycoon أو road_king.",
        "project_claimed": "المشروع سبق أن طالب به P{pid}. طالِب بمشروع مختلف.",
        "cannot_settle_before_play": "لا يمكن إجراء الحساب قبل اللعب (phase={phase}).",
        "settlement_pending_trade": "يتطلب الحساب الختامي حلّ الصفقة المعلّقة أولًا.",
        "invalid_player_id": "player_id غير صالح: {pid}",
        "unknown_controller_action": "إجراء وحدة تحكم غير معروف: {action}",
        "invalid_registration": "تسجيل اللاعب غير صالح أو مكرر.",
        "controller_connected": "وحدة التحكم متصلة بالفعل أو التسجيل غير صالح.",
        "internal_error": "خطأ داخلي في المحرك: {exc}",
        "action_complete": "إجراءك المجدول لهذا الدور قد اكتمل بالفعل. لخّص بإيجاز ما قمت به للتو، ثم أنهِ ردّك دون استدعاء أي أداة لإعادة السيطرة إلى المجدول.",
        "action_near_budget": "إجراءك المجدول لهذا الدور قد اكتمل بالفعل. لقد استهلكت كل الـ {budget} استدعاءات الإضافية المسموح بها في هذا الدور؛ استدعاء آخر سيُحتسب كـ forfeit. لخّص بإيجاز ما قمت به للتو، ثم أنهِ ردّك دون استدعاء أي أداة.",
        "action_over_budget": "خطأ: إجراءك المجدول لهذا الدور قد اكتمل بالفعل وقد تجاوزت حد {budget} استدعاءات لهذا الدور ({used} استدعاءات)؛ يُحتسب هذا الدور كـ forfeit. توقّف الآن — لخّص بإيجاز ثم أنهِ ردّك دون استدعاء أي أداة.",
        "turn_complete": "إجراءك المجدول لهذا الدور اكتمل. لخّص بإيجاز ما قمت به للتو، ثم أنهِ ردّك دون استدعاء أي أداة لإعادة السيطرة إلى المجدول.",
        "trade_quota_exhausted": "لقد استهلكت كل عروض الصفقة الـ {limit} المسموح بها في هذا الإيقاظ. لا تقترح صفقة أخرى؛ استخدم ما تبقّى من البناء أو مطالبة المشروع أو رسالة خاصة، أو أنهِ ردّك دون استدعاء أي أداة.",
        "chat_quota_exhausted": "لقد استهلكت كل الرسائل الخاصة الـ {limit} المسموح بها في هذا الإيقاظ. لا ترسل رسالة أخرى؛ استخدم ما تبقّى من البناء أو مطالبة المشروع أو عرض صفقة، أو أنهِ ردّك دون استدعاء أي أداة.",
        "wake_quota_note": (
            "يسمح هذا الإيقاظ ببناء واحد أو مطالبة مشروع واحدة (متضادتان)، وبحد أقصى "
            "{trade_limit} عروض صفقة و{chat_limit} رسالة خاصة. الصفقة والرسائل الخاصة خطوات "
            "حرة: لا تستهلك خانة البناء/المشروع وتظل متاحة قبله وبعده."
        ),
        "wake_scheduled": (
            "أنت مُجدول الآن بصفته TradeCraft P{pid}. اقرأ حالة اللعبة وأي تعليمات خطوة، ثم استخدم "
            "أدوات TradeCraft؛ ولا تكتفِ بشرح النقلة. بعد إكمال إجراءك المجدول — بناء ناجح، أو "
            "مطالبة بمشروع، أو خطوة تموضع — توقّف فورًا وأعد السيطرة إلى "
            "المُدير بإنهاء ردّك دون استدعاء أي أداة أخرى. أثناء اللعب العادي، يتوفر "
            "tradecraft_send_message للتفاوض الملموس كذكر الاحتياجات من الموارد أو الشروط المقبولة؛ "
            "وهو خطوة حرة لا تستهلك فعلك، فيمكنك مراسلة عدة لاعبين ثم التصرّف. "
            "إذا لم يتبقَّ أي إجراء قانوني منتج أو تفاوض جدير بالاهتمام، فلا تستدعِ أي أداة — اختم "
            "ردّك فحسب لإنهاء الدور. يجب دائمًا أن تُنتج إجراءً (استدعاء أداة) أو قرارًا صريحًا — "
            "فالردّ الفارغ أو الصامت غير مقبول أبدًا."
        ),
        "state_header": "حالة اللعبة الحالية",
        "resources_header": "مواردك (الحالية)",
        "resources_delta_header": "التغييرات منذ آخر استيقاظ لك:",
        "resources_delta_none": "لا تغيير",
        "events_header": "الأحداث منذ دورك الأخير",
        "tools_instruction": (
            "استخدم أدوات `tradecraft_*` الأربع التي يوفّرها ملحق اللعبة فقط. لإنهاء الدور، لا "
            "تستدعِ أي أداة — اختم ردّك فحسب."
        ),
        "map_topology_header": "طوبولوجيا الخريطة (ثابتة لهذه الجولة؛ غير قابلة للتغيير)",
        "player_identity": "أنت لاعب TradeCraft P{pid}. كن استراتيجيًّا وموجزًا وحاسمًا.",
        "operator_note": "مُدير المباراة هو المشغّل. اتبع تنبيهات الدور التي يرسلها.",
    },
}