/**
 * TradeCraft plugin UI-string catalog (tool labels / descriptions).
 *
 * Protocol values (tool names, enum values, resource keys, project ids) are
 * never localized — only the human-readable labels/descriptions are. A language
 * without a catalog entry falls back to English.
 */

export type TradeCraftLang = "en" | "zh" | "fr" | "es" | "ru" | "ar";

export interface ToolStrings {
  buildLabel: string;
  buildDescription: string[];
  proposeTradeLabel: string;
  proposeTradeDescription: string[];
  sendMessageLabel: string;
  sendMessageDescription: string[];
  claimProjectLabel: string;
  claimProjectDescription: string;
  sendMessageContentDescription: string;
}

const EN: ToolStrings = {
  buildLabel: "TradeCraft: Build",
  buildDescription: [
    "Build a road or settlement, or upgrade one of your buildings.",
    "Use type=road with edge_id, type=settlement with vertex_id, or type=upgrade with vertex_id and to_level.",
    "During initial placement, place a settlement and then an adjacent road.",
    "During play this is turn-gated and consumes one action when successful.",
  ],
  proposeTradeLabel: "TradeCraft: Propose Trade",
  proposeTradeDescription: [
    "Offer resources in exchange for resources held by another player.",
    "Set target to a player id (0-3); if omitted, the next player is selected.",
    "This is turn-gated and consumes one action when successful.",
  ],
  sendMessageLabel: "TradeCraft: Send Message",
  sendMessageDescription: [
    "Send a directed (private) negotiation message to one named player.",
    "Set target to a player id (0-3); the woken target's reply is returned to you inline in the same wake.",
    "This is a free step that does not consume your action, so you may message several players and then still act.",
  ],
  claimProjectLabel: "TradeCraft: Claim Project",
  claimProjectDescription:
    "Claim a first-come-first-served public project after its engine-side condition is met. This is turn-gated and consumes one action when successful.",
  sendMessageContentDescription: "Private message delivered to the target player, who is woken to reply within the same wake.",
};

const ZH: ToolStrings = {
  buildLabel: "TradeCraft：建造",
  buildDescription: [
    "修建道路或定居点，或升级你的某个建筑。",
    "建路用 type=road 配 edge_id，建定居点用 type=settlement 配 vertex_id，升级用 type=upgrade 配 vertex_id 和 to_level。",
    "初始摆子阶段，先放一个定居点，再放一条相邻道路。",
    "对局中该动作受回合门控，成功时消耗一次行动。",
  ],
  proposeTradeLabel: "TradeCraft：发起交易",
  proposeTradeDescription: [
    "用资源换取其他玩家持有的资源。",
    "target 设为某个玩家 id（0-3）；省略则默认选下一位玩家。",
    "该动作受回合门控，成功时消耗一次行动。",
  ],
  sendMessageLabel: "TradeCraft：发送消息",
  sendMessageDescription: [
    "向指定的某一位玩家发送定向（私聊）谈判消息。",
    "target 设为某个玩家 id（0-3）；被叫醒的目标会在同一回合内把回复内联返回给你。",
    "该动作**不消耗**本次叫醒的行动，因此你可以先依次私聊多名玩家，再执行你的动作。",
  ],
  claimProjectLabel: "TradeCraft：申请项目",
  claimProjectDescription:
    "引擎侧条件满足后，按先到先得申请某个公共项目。该动作受回合门控，成功时消耗一次行动。",
  sendMessageContentDescription: "发给目标玩家的私聊消息；对方被叫醒并在同一回合内回复。",
};

const FR: ToolStrings = {
  buildLabel: "TradeCraft : Construire",
  buildDescription: [
    "Construire une route ou une colonie, ou améliorer l'un de vos bâtiments.",
    "Utilisez type=road avec edge_id, type=settlement avec vertex_id, ou type=upgrade avec vertex_id et to_level.",
    "Pendant la phase de placement initial, placez une colonie puis une route adjacente.",
    "En cours de partie, cette action est soumise au tour et consomme une action en cas de succès.",
  ],
  proposeTradeLabel: "TradeCraft : Proposer un échange",
  proposeTradeDescription: [
    "Offrir des ressources en échange de ressources détenues par un autre joueur.",
    "Définissez target sur un identifiant de joueur (0-3) ; s'il est omis, le joueur suivant est choisi.",
    "Cette action est soumise au tour et consomme une action en cas de succès.",
  ],
  sendMessageLabel: "TradeCraft : Envoyer un message",
  sendMessageDescription: [
    "Envoyer un message de négociation ciblé (privé) à un joueur nommé.",
    "Réglez target sur un id de joueur (0-3) ; la réponse du destinataire réveillé vous revient en ligne dans le même réveil.",
    "C'est une étape libre qui ne consomme pas votre action : vous pouvez écrire à plusieurs joueurs puis agir.",
  ],
  claimProjectLabel: "TradeCraft : Réclamer un projet",
  claimProjectDescription:
    "Réclamer un projet public (premier arrivé, premier servi) une fois sa condition côté moteur satisfaite. Cette action est soumise au tour et consomme une action en cas de succès.",
  sendMessageContentDescription: "Message privé livré au joueur ciblé, réveillé pour répondre dans le même tour.",
};

const ES: ToolStrings = {
  buildLabel: "TradeCraft: Construir",
  buildDescription: [
    "Construir una carretera o un poblado, o mejorar uno de tus edificios.",
    "Usa type=road con edge_id, type=settlement con vertex_id, o type=upgrade con vertex_id y to_level.",
    "Durante la colocación inicial, coloca un poblado y luego una carretera adyacente.",
    "En partida, esta acción está sujeta al turno y consume una acción si tiene éxito.",
  ],
  proposeTradeLabel: "TradeCraft: Proponer intercambio",
  proposeTradeDescription: [
    "Ofrecer recursos a cambio de recursos que tenga otro jugador.",
    "Pon target en un id de jugador (0-3); si se omite, se elige al siguiente jugador.",
    "Esta acción está sujeta al turno y consume una acción si tiene éxito.",
  ],
  sendMessageLabel: "TradeCraft: Enviar mensaje",
  sendMessageDescription: [
    "Enviar un mensaje de negociación dirigido (privado) a un jugador concreto.",
    "Fija target a un id de jugador (0-3); la respuesta del destinatario despertado se te devuelve en línea en el mismo despertar.",
    "Es un paso libre que no consume tu acción: puedes escribir a varios jugadores y luego actuar.",
  ],
  claimProjectLabel: "TradeCraft: Reclamar proyecto",
  claimProjectDescription:
    "Reclamar un proyecto público (por orden de llegada) una vez que se cumpla su condición en el motor. Esta acción está sujeta al turno y consume una acción si tiene éxito.",
  sendMessageContentDescription: "Mensaje privado entregado al jugador objetivo, que es despertado para responder en el mismo turno.",
};

const RU: ToolStrings = {
  buildLabel: "TradeCraft: Построить",
  buildDescription: [
    "Построить дорогу или поселение или улучшить одно из ваших зданий.",
    "Используйте type=road с edge_id, type=settlement с vertex_id или type=upgrade с vertex_id и to_level.",
    "На этапе начальной расстановки поставьте поселение, затем прилегающую дорогу.",
    "В игре действие ограничено ходом и тратит одно действие при успехе.",
  ],
  proposeTradeLabel: "TradeCraft: Предложить обмен",
  proposeTradeDescription: [
    "Предложить ресурсы в обмен на ресурсы другого игрока.",
    "Укажите target как id игрока (0–3); если не указан, выбирается следующий игрок.",
    "Действие ограничено ходом и тратит одно действие при успехе.",
  ],
  sendMessageLabel: "TradeCraft: Отправить сообщение",
  sendMessageDescription: [
    "Отправить адресное (приватное) сообщение о переговорах одному игроку.",
    "Укажите target как id игрока (0-3); ответ разбуженного адресата вернётся вам встроенно в том же пробуждении.",
    "Это бесплатный шаг, не расходующий действие: можно написать нескольким игрокам и затем действовать.",
  ],
  claimProjectLabel: "TradeCraft: Заявить проект",
  claimProjectDescription:
    "Заявить публичный проект (первым пришёл — первым получил) после выполнения его условия на стороне движка. Действие ограничено ходом и тратит одно действие при успехе.",
  sendMessageContentDescription:
    "Личное сообщение, доставляемое целевому игроку, которого будят для ответа в том же ходе.",
};

const AR: ToolStrings = {
  buildLabel: "TradeCraft: بناء",
  buildDescription: [
    "بناء طريق أو مستوطنة، أو ترقية أحد مبانيك.",
    "استخدم type=road مع edge_id، أو type=settlement مع vertex_id، أو type=upgrade مع vertex_id و to_level.",
    "أثناء التموضع الأولي، ضع مستوطنة ثم طريقًا مجاورًا.",
    "أثناء اللعب يُقيَّد هذا الإجراء بالدور ويستهلك إجراءً واحدًا عند النجاح.",
  ],
  proposeTradeLabel: "TradeCraft: اقتراح صفقة",
  proposeTradeDescription: [
    "عرض موارد مقابل موارد يملكها لاعب آخر.",
    "عيِّن target إلى معرّف لاعب (0-3)؛ وإن حُذف يُختار اللاعب التالي.",
    "يُقيَّد هذا الإجراء بالدور ويستهلك إجراءً واحدًا عند النجاح.",
  ],
  sendMessageLabel: "TradeCraft: إرسال رسالة",
  sendMessageDescription: [
    "أرسل رسالة تفاوض موجَّهة (خاصة) إلى لاعب واحد محدَّد.",
    "اضبط target على معرّف لاعب (0-3)؛ ويُعاد إليك ردّ اللاعب المُوقَظ مضمَّنًا في الإيقاظ نفسه.",
    "هذه خطوة حرة لا تستهلك فعلك: يمكنك مراسلة عدة لاعبين ثم التصرّف.",
  ],
  claimProjectLabel: "TradeCraft: المطالبة بمشروع",
  claimProjectDescription:
    "المطالبة بمشروع عام (من يأتي أولًا يُخدم أولًا) بعد تحقق شرطه في المحرك. يُقيَّد هذا الإجراء بالدور ويستهلك إجراءً واحدًا عند النجاح.",
  sendMessageContentDescription: "رسالة خاصة تُسلَّم إلى اللاعب الهدف، الذي يُوقَظ للردّ في الدور نفسه.",
};

const CATALOGS: Partial<Record<TradeCraftLang, ToolStrings>> = {
  en: EN,
  zh: ZH,
  fr: FR,
  es: ES,
  ru: RU,
  ar: AR,
};

export function ui(lang: string | undefined): ToolStrings {
  const l = (lang || "en") as TradeCraftLang;
  return CATALOGS[l] ?? EN;
}