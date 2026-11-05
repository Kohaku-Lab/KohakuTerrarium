// examples/packages/live2d_pet/vendor/Framework/src/id/cubismid.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
/**
* パラメータ名・パーツ名・Drawable名を保持
*
* パラメータ名・パーツ名・Drawable名を保持するクラス。
*
* @note 指定したID文字列からCubismIdを取得する際はこのクラスの生成メソッドを呼ばず、
*       CubismIdManager().getId(id)を使用してください
*/
var CubismId = class CubismId {
	/**
	* 内部で使用するCubismIdクラス生成メソッド
	*
	* @param id ID文字列
	* @return CubismId
	* @note 指定したID文字列からCubismIdを取得する際は
	*       CubismIdManager().getId(id)を使用してください
	*/
	static createIdInternal(id) {
		return new CubismId(id);
	}
	/**
	* ID名を取得する
	*/
	getString() {
		return this._id;
	}
	/**
	* idを比較
	* @param c 比較するid
	* @return 同じならばtrue,異なっていればfalseを返す
	*/
	isEqual(c) {
		if (typeof c === "string") return this._id == c;
		else if (c instanceof CubismId) return this._id == c._id;
		return false;
	}
	/**
	* idを比較
	* @param c 比較するid
	* @return 同じならばtrue,異なっていればfalseを返す
	*/
	isNotEqual(c) {
		if (typeof c == "string") return !(this._id == c);
		else if (c instanceof CubismId) return !(this._id == c._id);
		return false;
	}
	/**
	* プライベートコンストラクタ
	*
	* @note ユーザーによる生成は許可しません
	*/
	constructor(id) {
		this._id = id;
	}
};
var Live2DCubismFramework$36;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismId = CubismId;
})(Live2DCubismFramework$36 || (Live2DCubismFramework$36 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/id/cubismidmanager.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
/**
* ID名の管理
*
* ID名を管理する。
*/
var CubismIdManager = class {
	/**
	* コンストラクタ
	*/
	constructor() {
		this._ids = new Array();
	}
	/**
	* デストラクタ相当の処理
	*/
	release() {
		for (let i = 0; i < this._ids.length; ++i) this._ids[i] = void 0;
		this._ids = null;
	}
	/**
	* ID名をリストから登録
	*
	* @param ids ID名リスト
	* @param count IDの個数
	*/
	registerIds(ids) {
		for (let i = 0; i < ids.length; i++) this.registerId(ids[i]);
	}
	/**
	* ID名を登録
	*
	* @param id ID名
	*/
	registerId(id) {
		let result = null;
		if ("string" == typeof id) {
			if ((result = this.findId(id)) != null) return result;
			result = CubismId.createIdInternal(id);
			this._ids.push(result);
		} else return this.registerId(id);
		return result;
	}
	/**
	* ID名からIDを取得する
	*
	* @param id ID名
	*/
	getId(id) {
		return this.registerId(id);
	}
	/**
	* ID名からIDの確認
	*
	* @return true 存在する
	* @return false 存在しない
	*/
	isExist(id) {
		if ("string" == typeof id) return this.findId(id) != null;
		return this.isExist(id);
	}
	/**
	* ID名からIDを検索する。
	*
	* @param id ID名
	* @return 登録されているID。なければNULL。
	*/
	findId(id) {
		for (let i = 0; i < this._ids.length; ++i) if (this._ids[i].getString() == id) return this._ids[i];
		return null;
	}
};
var Live2DCubismFramework$35;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismIdManager = CubismIdManager;
})(Live2DCubismFramework$35 || (Live2DCubismFramework$35 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/math/cubismvector2.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
/**
* 2次元ベクトル型
*
* 2次元ベクトル型の機能を提供する。
*/
var CubismVector2 = class CubismVector2 {
	/**
	* コンストラクタ
	*/
	constructor(x, y) {
		this.x = x;
		this.y = y;
		this.x = x == void 0 ? 0 : x;
		this.y = y == void 0 ? 0 : y;
	}
	/**
	* ベクトルの加算
	*
	* @param vector2 加算するベクトル値
	* @return 加算結果 ベクトル値
	*/
	add(vector2) {
		const ret = new CubismVector2(0, 0);
		ret.x = this.x + vector2.x;
		ret.y = this.y + vector2.y;
		return ret;
	}
	/**
	* ベクトルの減算
	*
	* @param vector2 減算するベクトル値
	* @return 減算結果 ベクトル値
	*/
	substract(vector2) {
		const ret = new CubismVector2(0, 0);
		ret.x = this.x - vector2.x;
		ret.y = this.y - vector2.y;
		return ret;
	}
	/**
	* ベクトルの乗算
	*
	* @param vector2 乗算するベクトル値
	* @return 乗算結果 ベクトル値
	*/
	multiply(vector2) {
		const ret = new CubismVector2(0, 0);
		ret.x = this.x * vector2.x;
		ret.y = this.y * vector2.y;
		return ret;
	}
	/**
	* ベクトルの乗算(スカラー)
	*
	* @param scalar 乗算するスカラー値
	* @return 乗算結果 ベクトル値
	*/
	multiplyByScaler(scalar) {
		return this.multiply(new CubismVector2(scalar, scalar));
	}
	/**
	* ベクトルの除算
	*
	* @param vector2 除算するベクトル値
	* @return 除算結果 ベクトル値
	*/
	division(vector2) {
		const ret = new CubismVector2(0, 0);
		ret.x = this.x / vector2.x;
		ret.y = this.y / vector2.y;
		return ret;
	}
	/**
	* ベクトルの除算(スカラー)
	*
	* @param scalar 除算するスカラー値
	* @return 除算結果 ベクトル値
	*/
	divisionByScalar(scalar) {
		return this.division(new CubismVector2(scalar, scalar));
	}
	/**
	* ベクトルの長さを取得する
	*
	* @return ベクトルの長さ
	*/
	getLength() {
		return Math.sqrt(this.x * this.x + this.y * this.y);
	}
	/**
	* ベクトルの距離の取得
	*
	* @param a 点
	* @return ベクトルの距離
	*/
	getDistanceWith(a) {
		return Math.sqrt((this.x - a.x) * (this.x - a.x) + (this.y - a.y) * (this.y - a.y));
	}
	/**
	* ドット積の計算
	*
	* @param a 値
	* @return 結果
	*/
	dot(a) {
		return this.x * a.x + this.y * a.y;
	}
	/**
	* 正規化の適用
	*/
	normalize() {
		const length = Math.pow(this.x * this.x + this.y * this.y, .5);
		this.x = this.x / length;
		this.y = this.y / length;
	}
	/**
	* 等しさの確認（等しいか？）
	*
	* 値が等しいか？
	*
	* @param rhs 確認する値
	* @return true 値は等しい
	* @return false 値は等しくない
	*/
	isEqual(rhs) {
		return this.x == rhs.x && this.y == rhs.y;
	}
	/**
	* 等しさの確認（等しくないか？）
	*
	* 値が等しくないか？
	*
	* @param rhs 確認する値
	* @return true 値は等しくない
	* @return false 値は等しい
	*/
	isNotEqual(rhs) {
		return !this.isEqual(rhs);
	}
};
var Live2DCubismFramework$34;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismVector2 = CubismVector2;
})(Live2DCubismFramework$34 || (Live2DCubismFramework$34 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/math/cubismmath.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
/**
* 数値計算などに使用するユーティリティクラス
*/
var CubismMath = class CubismMath {
	static {
		this.Epsilon = 1e-5;
	}
	/**
	* 第一引数の値を最小値と最大値の範囲に収めた値を返す
	*
	* @param value 収められる値
	* @param min   範囲の最小値
	* @param max   範囲の最大値
	* @return 最小値と最大値の範囲に収めた値
	*/
	static range(value, min, max) {
		if (value < min) value = min;
		else if (value > max) value = max;
		return value;
	}
	/**
	* サイン関数の値を求める
	*
	* @param x 角度値（ラジアン）
	* @return サイン関数sin(x)の値
	*/
	static sin(x) {
		return Math.sin(x);
	}
	/**
	* コサイン関数の値を求める
	*
	* @param x 角度値(ラジアン)
	* @return コサイン関数cos(x)の値
	*/
	static cos(x) {
		return Math.cos(x);
	}
	/**
	* 値の絶対値を求める
	*
	* @param x 絶対値を求める値
	* @return 値の絶対値
	*/
	static abs(x) {
		return Math.abs(x);
	}
	/**
	* 平方根(ルート)を求める
	* @param x -> 平方根を求める値
	* @return 値の平方根
	*/
	static sqrt(x) {
		return Math.sqrt(x);
	}
	/**
	* 立方根を求める
	* @param x -> 立方根を求める値
	* @return 値の立方根
	*/
	static cbrt(x) {
		if (x === 0) return x;
		let cx = x;
		const isNegativeNumber = cx < 0;
		if (isNegativeNumber) cx = -cx;
		let ret;
		if (cx === Infinity) ret = Infinity;
		else {
			ret = Math.exp(Math.log(cx) / 3);
			ret = (cx / (ret * ret) + 2 * ret) / 3;
		}
		return isNegativeNumber ? -ret : ret;
	}
	/**
	* イージング処理されたサインを求める
	* フェードイン・アウト時のイージングに利用できる
	*
	* @param value イージングを行う値
	* @return イージング処理されたサイン値
	*/
	static getEasingSine(value) {
		if (value < 0) return 0;
		else if (value > 1) return 1;
		return .5 - .5 * this.cos(value * Math.PI);
	}
	/**
	* 大きい方の値を返す
	*
	* @param left 左辺の値
	* @param right 右辺の値
	* @return 大きい方の値
	*/
	static max(left, right) {
		return left > right ? left : right;
	}
	/**
	* 小さい方の値を返す
	*
	* @param left  左辺の値
	* @param right 右辺の値
	* @return 小さい方の値
	*/
	static min(left, right) {
		return left > right ? right : left;
	}
	static clamp(val, min, max) {
		if (val < min) return min;
		else if (max < val) return max;
		return val;
	}
	/**
	* 角度値をラジアン値に変換する
	*
	* @param degrees   角度値
	* @return 角度値から変換したラジアン値
	*/
	static degreesToRadian(degrees) {
		return degrees / 180 * Math.PI;
	}
	/**
	* ラジアン値を角度値に変換する
	*
	* @param radian    ラジアン値
	* @return ラジアン値から変換した角度値
	*/
	static radianToDegrees(radian) {
		return radian * 180 / Math.PI;
	}
	/**
	* ２つのベクトルからラジアン値を求める
	*
	* @param from  始点ベクトル
	* @param to    終点ベクトル
	* @return ラジアン値から求めた方向ベクトル
	*/
	static directionToRadian(from, to) {
		let ret = Math.atan2(to.y, to.x) - Math.atan2(from.y, from.x);
		while (ret < -Math.PI) ret += Math.PI * 2;
		while (ret > Math.PI) ret -= Math.PI * 2;
		return ret;
	}
	/**
	* ２つのベクトルから角度値を求める
	*
	* @param from  始点ベクトル
	* @param to    終点ベクトル
	* @return 角度値から求めた方向ベクトル
	*/
	static directionToDegrees(from, to) {
		const radian = this.directionToRadian(from, to);
		let degree = this.radianToDegrees(radian);
		if (to.x - from.x > 0) degree = -degree;
		return degree;
	}
	/**
	* ラジアン値を方向ベクトルに変換する。
	*
	* @param totalAngle    ラジアン値
	* @return ラジアン値から変換した方向ベクトル
	*/
	static radianToDirection(totalAngle) {
		const ret = new CubismVector2();
		ret.x = this.sin(totalAngle);
		ret.y = this.cos(totalAngle);
		return ret;
	}
	/**
	* 三次方程式の三次項の係数が0になったときに補欠的に二次方程式の解をもとめる。
	* a * x^2 + b * x + c = 0
	*
	* @param   a -> 二次項の係数値
	* @param   b -> 一次項の係数値
	* @param   c -> 定数項の値
	* @return  二次方程式の解
	*/
	static quadraticEquation(a, b, c) {
		if (this.abs(a) < CubismMath.Epsilon) {
			if (this.abs(b) < CubismMath.Epsilon) return -c;
			return -c / b;
		}
		return -(b + this.sqrt(b * b - 4 * a * c)) / (2 * a);
	}
	/**
	* カルダノの公式によってベジェのt値に該当する３次方程式の解を求める。
	* 重解になったときには0.0～1.0の値になる解を返す。
	*
	* a * x^3 + b * x^2 + c * x + d = 0
	*
	* @param   a -> 三次項の係数値
	* @param   b -> 二次項の係数値
	* @param   c -> 一次項の係数値
	* @param   d -> 定数項の値
	* @return  0.0～1.0の間にある解
	*/
	static cardanoAlgorithmForBezier(a, b, c, d) {
		if (this.abs(a) < CubismMath.Epsilon) return this.range(this.quadraticEquation(b, c, d), 0, 1);
		const ba = b / a;
		const ca = c / a;
		const da = d / a;
		const p = (3 * ca - ba * ba) / 3;
		const p3 = p / 3;
		const q = (2 * ba * ba * ba - 9 * ba * ca + 27 * da) / 27;
		const q2 = q / 2;
		const discriminant = q2 * q2 + p3 * p3 * p3;
		const center = .5;
		const threshold = .51;
		if (discriminant < 0) {
			const mp3 = -p / 3;
			const mp33 = mp3 * mp3 * mp3;
			const r = this.sqrt(mp33);
			const t = -q / (2 * r);
			const cosphi = this.range(t, -1, 1);
			const phi = Math.acos(cosphi);
			const t1 = 2 * this.cbrt(r);
			const root1 = t1 * this.cos(phi / 3) - ba / 3;
			if (this.abs(root1 - center) < threshold) return this.range(root1, 0, 1);
			const root2 = t1 * this.cos((phi + 2 * Math.PI) / 3) - ba / 3;
			if (this.abs(root2 - center) < threshold) return this.range(root2, 0, 1);
			const root3 = t1 * this.cos((phi + 4 * Math.PI) / 3) - ba / 3;
			return this.range(root3, 0, 1);
		}
		if (discriminant == 0) {
			let u1;
			if (q2 < 0) u1 = this.cbrt(-q2);
			else u1 = -this.cbrt(q2);
			const root1 = 2 * u1 - ba / 3;
			if (this.abs(root1 - center) < threshold) return this.range(root1, 0, 1);
			const root2 = -u1 - ba / 3;
			return this.range(root2, 0, 1);
		}
		const sd = this.sqrt(discriminant);
		const root1 = this.cbrt(sd - q2) - this.cbrt(sd + q2) - ba / 3;
		return this.range(root1, 0, 1);
	}
	/**
	* 浮動小数点の余りを求める。
	*
	* @param dividend 被除数（割られる値）
	* @param divisor 除数（割る値）
	* @return 余り
	*/
	static mod(dividend, divisor) {
		if (!isFinite(dividend) || divisor === 0 || isNaN(dividend) || isNaN(divisor)) {
			console.warn(`divided: ${dividend}, divisor: ${divisor} mod() returns 'NaN'.`);
			return NaN;
		}
		const absDividend = Math.abs(dividend);
		const absDivisor = Math.abs(divisor);
		let result = absDividend - Math.floor(absDividend / absDivisor) * absDivisor;
		result *= Math.sign(dividend);
		return result;
	}
	/**
	* コンストラクタ
	*/
	constructor() {}
};
var Live2DCubismFramework$33;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismMath = CubismMath;
})(Live2DCubismFramework$33 || (Live2DCubismFramework$33 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/math/cubismmatrix44.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
/**
* 4x4の行列
*
* 4x4行列の便利クラス。
*/
var CubismMatrix44 = class CubismMatrix44 {
	/**
	* コンストラクタ
	*/
	constructor() {
		this._tr = /* @__PURE__ */ new Float32Array(16);
		this.loadIdentity();
	}
	/**
	* 受け取った２つの行列の乗算を行う。
	*
	* @param a 行列a
	* @param b 行列b
	*
	* @return 乗算結果の行列
	*/
	static multiply(a, b, dst) {
		const c = new Float32Array([
			0,
			0,
			0,
			0,
			0,
			0,
			0,
			0,
			0,
			0,
			0,
			0,
			0,
			0,
			0,
			0
		]);
		const n = 4;
		for (let i = 0; i < n; ++i) for (let j = 0; j < n; ++j) for (let k = 0; k < n; ++k) c[j + i * 4] += a[k + i * 4] * b[j + k * 4];
		for (let i = 0; i < 16; ++i) dst[i] = c[i];
	}
	/**
	* 単位行列に初期化する
	*/
	loadIdentity() {
		const c = new Float32Array([
			1,
			0,
			0,
			0,
			0,
			1,
			0,
			0,
			0,
			0,
			1,
			0,
			0,
			0,
			0,
			1
		]);
		this.setMatrix(c);
	}
	/**
	* 行列を設定
	*
	* @param tr 16個の浮動小数点数で表される4x4の行列
	*/
	setMatrix(tr) {
		for (let i = 0; i < 16; ++i) this._tr[i] = tr[i];
	}
	/**
	* 行列を浮動小数点数の配列で取得
	*
	* @return 16個の浮動小数点数で表される4x4の行列
	*/
	getArray() {
		return this._tr;
	}
	/**
	* X軸の拡大率を取得
	*
	* @return X軸の拡大率
	*/
	getScaleX() {
		return this._tr[0];
	}
	/**
	* Y軸の拡大率を取得する
	*
	* @return Y軸の拡大率
	*/
	getScaleY() {
		return this._tr[5];
	}
	/**
	* X軸の移動量を取得
	*
	* @return X軸の移動量
	*/
	getTranslateX() {
		return this._tr[12];
	}
	/**
	* Y軸の移動量を取得
	*
	* @return Y軸の移動量
	*/
	getTranslateY() {
		return this._tr[13];
	}
	/**
	* X軸の値を現在の行列で計算
	*
	* @param src X軸の値
	*
	* @return 現在の行列で計算されたX軸の値
	*/
	transformX(src) {
		return this._tr[0] * src + this._tr[12];
	}
	/**
	* Y軸の値を現在の行列で計算
	*
	* @param src Y軸の値
	*
	* @return 現在の行列で計算されたY軸の値
	*/
	transformY(src) {
		return this._tr[5] * src + this._tr[13];
	}
	/**
	* X軸の値を現在の行列で逆計算
	*/
	invertTransformX(src) {
		return (src - this._tr[12]) / this._tr[0];
	}
	/**
	* Y軸の値を現在の行列で逆計算
	*/
	invertTransformY(src) {
		return (src - this._tr[13]) / this._tr[5];
	}
	/**
	* 現在の行列の位置を起点にして移動
	*
	* 現在の行列の位置を起点にして相対的に移動する。
	*
	* @param x X軸の移動量
	* @param y Y軸の移動量
	*/
	translateRelative(x, y) {
		const tr1 = new Float32Array([
			1,
			0,
			0,
			0,
			0,
			1,
			0,
			0,
			0,
			0,
			1,
			0,
			x,
			y,
			0,
			1
		]);
		CubismMatrix44.multiply(tr1, this._tr, this._tr);
	}
	/**
	* 現在の行列の位置を移動
	*
	* 現在の行列の位置を指定した位置へ移動する
	*
	* @param x X軸の移動量
	* @param y y軸の移動量
	*/
	translate(x, y) {
		this._tr[12] = x;
		this._tr[13] = y;
	}
	/**
	* 現在の行列のX軸の位置を指定した位置へ移動する
	*
	* @param x X軸の移動量
	*/
	translateX(x) {
		this._tr[12] = x;
	}
	/**
	* 現在の行列のY軸の位置を指定した位置へ移動する
	*
	* @param y Y軸の移動量
	*/
	translateY(y) {
		this._tr[13] = y;
	}
	/**
	* 現在の行列の拡大率を相対的に設定する
	*
	* @param x X軸の拡大率
	* @param y Y軸の拡大率
	*/
	scaleRelative(x, y) {
		const tr1 = new Float32Array([
			x,
			0,
			0,
			0,
			0,
			y,
			0,
			0,
			0,
			0,
			1,
			0,
			0,
			0,
			0,
			1
		]);
		CubismMatrix44.multiply(tr1, this._tr, this._tr);
	}
	/**
	* 現在の行列の拡大率を指定した倍率に設定する
	*
	* @param x X軸の拡大率
	* @param y Y軸の拡大率
	*/
	scale(x, y) {
		this._tr[0] = x;
		this._tr[5] = y;
	}
	/**
	* 引数で与えられた行列にこの行列を乗算する。
	* (引数で与えられた行列) * (この行列)
	*
	* @note 関数名と実際の計算内容に乖離があるため、今後計算順が修正される可能性があります。
	* @param m 行列
	*/
	multiplyByMatrix(m) {
		CubismMatrix44.multiply(m.getArray(), this._tr, this._tr);
	}
	/**
	* 現在の行列の逆行列を求める。
	*
	* @return 現在の行列で計算された逆行列の値を返す
	*/
	getInvert() {
		const r00 = this._tr[0];
		const r10 = this._tr[1];
		const r20 = this._tr[2];
		const r01 = this._tr[4];
		const r11 = this._tr[5];
		const r21 = this._tr[6];
		const r02 = this._tr[8];
		const r12 = this._tr[9];
		const r22 = this._tr[10];
		const tx = this._tr[12];
		const ty = this._tr[13];
		const tz = this._tr[14];
		const det = r00 * (r11 * r22 - r12 * r21) - r01 * (r10 * r22 - r12 * r20) + r02 * (r10 * r21 - r11 * r20);
		const dst = new CubismMatrix44();
		if (CubismMath.abs(det) < CubismMath.Epsilon) {
			dst.loadIdentity();
			return dst;
		}
		const invDet = 1 / det;
		const inv00 = (r11 * r22 - r12 * r21) * invDet;
		const inv01 = -(r01 * r22 - r02 * r21) * invDet;
		const inv02 = (r01 * r12 - r02 * r11) * invDet;
		const inv10 = -(r10 * r22 - r12 * r20) * invDet;
		const inv11 = (r00 * r22 - r02 * r20) * invDet;
		const inv12 = -(r00 * r12 - r02 * r10) * invDet;
		const inv20 = (r10 * r21 - r11 * r20) * invDet;
		const inv21 = -(r00 * r21 - r01 * r20) * invDet;
		const inv22 = (r00 * r11 - r01 * r10) * invDet;
		dst._tr[0] = inv00;
		dst._tr[1] = inv10;
		dst._tr[2] = inv20;
		dst._tr[3] = 0;
		dst._tr[4] = inv01;
		dst._tr[5] = inv11;
		dst._tr[6] = inv21;
		dst._tr[7] = 0;
		dst._tr[8] = inv02;
		dst._tr[9] = inv12;
		dst._tr[10] = inv22;
		dst._tr[11] = 0;
		dst._tr[12] = -(inv00 * tx + inv01 * ty + inv02 * tz);
		dst._tr[13] = -(inv10 * tx + inv11 * ty + inv12 * tz);
		dst._tr[14] = -(inv20 * tx + inv21 * ty + inv22 * tz);
		dst._tr[15] = 1;
		return dst;
	}
	/**
	* オブジェクトのコピーを生成する
	*/
	clone() {
		const cloneMatrix = new CubismMatrix44();
		for (let i = 0; i < this._tr.length; i++) cloneMatrix._tr[i] = this._tr[i];
		return cloneMatrix;
	}
};
var Live2DCubismFramework$32;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismMatrix44 = CubismMatrix44;
})(Live2DCubismFramework$32 || (Live2DCubismFramework$32 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/type/csmrectf.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
/**
* 矩形形状（座標・長さはfloat値）を定義するクラス
*/
var csmRect = class {
	/**
	* コンストラクタ
	* @param x 左端X座標
	* @param y 上端Y座標
	* @param w 幅
	* @param h 高さ
	*/
	constructor(x, y, w, h) {
		this.x = x;
		this.y = y;
		this.width = w;
		this.height = h;
	}
	/**
	* 矩形中央のX座標を取得する
	*/
	getCenterX() {
		return this.x + .5 * this.width;
	}
	/**
	* 矩形中央のY座標を取得する
	*/
	getCenterY() {
		return this.y + .5 * this.height;
	}
	/**
	* 右側のX座標を取得する
	*/
	getRight() {
		return this.x + this.width;
	}
	/**
	* 下端のY座標を取得する
	*/
	getBottom() {
		return this.y + this.height;
	}
	/**
	* 矩形に値をセットする
	* @param r 矩形のインスタンス
	*/
	setRect(r) {
		this.x = r.x;
		this.y = r.y;
		this.width = r.width;
		this.height = r.height;
	}
	/**
	* 矩形中央を軸にして縦横を拡縮する
	* @param w 幅方向に拡縮する量
	* @param h 高さ方向に拡縮する量
	*/
	expand(w, h) {
		this.x -= w;
		this.y -= h;
		this.width += w * 2;
		this.height += h * 2;
	}
};
var Live2DCubismFramework$31;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.csmRect = csmRect;
})(Live2DCubismFramework$31 || (Live2DCubismFramework$31 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/utils/cubismdebug.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
var CubismLogPrint = (level, fmt, args) => {
	CubismDebug.print(level, "[CSM]" + fmt, args);
};
var CubismLogPrintIn = (level, fmt, args) => {
	CubismLogPrint(level, fmt + "\n", args);
};
var CSM_ASSERT = (expr) => {
	console.assert(expr);
};
var CubismLogDebug;
var CubismLogInfo;
var CubismLogWarning;
var CubismLogError;
CubismLogDebug = (fmt, ...args) => {
	CubismLogPrintIn(LogLevel.LogLevel_Debug, "[D]" + fmt, args);
};
CubismLogInfo = (fmt, ...args) => {
	CubismLogPrintIn(LogLevel.LogLevel_Info, "[I]" + fmt, args);
};
CubismLogWarning = (fmt, ...args) => {
	CubismLogPrintIn(LogLevel.LogLevel_Warning, "[W]" + fmt, args);
};
CubismLogError = (fmt, ...args) => {
	CubismLogPrintIn(LogLevel.LogLevel_Error, "[E]" + fmt, args);
};
/**
* デバッグ用のユーティリティクラス。
* ログの出力、バイトのダンプなど
*/
var CubismDebug = class {
	/**
	* ログを出力する。第一引数にログレベルを設定する。
	* CubismFramework.initialize()時にオプションで設定されたログ出力レベルを下回る場合はログに出さない。
	*
	* @param logLevel ログレベルの設定
	* @param format 書式付き文字列
	* @param args 可変長引数
	*/
	static print(logLevel, format, args) {
		if (logLevel < CubismFramework.getLoggingLevel()) return;
		const logPrint = CubismFramework.coreLogFunction;
		if (!logPrint) return;
		logPrint(format.replace(/\{(\d+)\}/g, (m, k) => {
			return args[k];
		}));
	}
	/**
	* データから指定した長さだけダンプ出力する。
	* CubismFramework.initialize()時にオプションで設定されたログ出力レベルを下回る場合はログに出さない。
	*
	* @param logLevel ログレベルの設定
	* @param data ダンプするデータ
	* @param length ダンプする長さ
	*/
	static dumpBytes(logLevel, data, length) {
		for (let i = 0; i < length; i++) {
			if (i % 16 == 0 && i > 0) this.print(logLevel, "\n");
			else if (i % 8 == 0 && i > 0) this.print(logLevel, "  ");
			this.print(logLevel, "{0} ", [data[i] & 255]);
		}
		this.print(logLevel, "\n");
	}
	/**
	* private コンストラクタ
	*/
	constructor() {}
};
var Live2DCubismFramework$30;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismDebug = CubismDebug;
})(Live2DCubismFramework$30 || (Live2DCubismFramework$30 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/rendering/cubismrenderer.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
/**
* モデル描画を処理するレンダラ
*
* サブクラスに環境依存の描画命令を記述する。
*/
var CubismRenderer = class {
	/**
	* レンダラのインスタンスを生成して取得する
	*
	* @return レンダラのインスタンス
	*/
	static create() {
		return null;
	}
	/**
	* レンダラのインスタンスを解放する
	*/
	static delete(renderer) {
		renderer = null;
	}
	/**
	* レンダラの初期化処理を実行する
	* 引数に渡したモデルからレンダラの初期化処理に必要な情報を取り出すことができる
	*
	* @param model モデルのインスタンス
	*/
	initialize(model) {
		this._model = model;
		if (model.isBlendModeEnabled()) {
			this.useHighPrecisionMask(true);
			CubismLogInfo("This model uses a high-resolution mask because it operates in blend mode.");
		}
	}
	/**
	* モデルを描画する
	* @param shaderPath ブレンドモード用シェーダのパス
	*/
	drawModel(shaderPath = null) {
		if (this.getModel() == null) return;
		this.doDrawModel(shaderPath);
	}
	/**
	* Model-View-Projection 行列をセットする
	* 配列は複製されるので、元の配列は外で破棄して良い
	*
	* @param matrix44 Model-View-Projection 行列
	*/
	setMvpMatrix(matrix44) {
		this._mvpMatrix4x4.setMatrix(matrix44.getArray());
	}
	/**
	* Model-View-Projection 行列を取得する
	*
	* @return Model-View-Projection 行列
	*/
	getMvpMatrix() {
		return this._mvpMatrix4x4;
	}
	/**
	* モデルの色をセットする
	* 各色0.0~1.0の間で指定する（1.0が標準の状態）
	*
	* @param red 赤チャンネルの値
	* @param green 緑チャンネルの値
	* @param blue 青チャンネルの値
	* @param alpha αチャンネルの値
	*/
	setModelColor(red, green, blue, alpha) {
		this._modelColor.r = CubismMath.clamp(red, 0, 1);
		this._modelColor.g = CubismMath.clamp(green, 0, 1);
		this._modelColor.b = CubismMath.clamp(blue, 0, 1);
		this._modelColor.a = CubismMath.clamp(alpha, 0, 1);
	}
	/**
	* モデルの色を取得する
	* 各色0.0~1.0の間で指定する(1.0が標準の状態)
	*
	* @return RGBAのカラー情報
	*/
	getModelColor() {
		return JSON.parse(JSON.stringify(this._modelColor));
	}
	/**
	* 透明度を考慮したモデルの色を計算する。
	*
	* @param opacity 透明度
	*
	* @return RGBAのカラー情報
	*/
	getModelColorWithOpacity(opacity) {
		const modelColorRGBA = this.getModelColor();
		modelColorRGBA.a *= opacity;
		if (this.isPremultipliedAlpha()) {
			modelColorRGBA.r *= modelColorRGBA.a;
			modelColorRGBA.g *= modelColorRGBA.a;
			modelColorRGBA.b *= modelColorRGBA.a;
		}
		return modelColorRGBA;
	}
	/**
	* 乗算済みαの有効・無効をセットする
	* 有効にするならtrue、無効にするならfalseをセットする
	*/
	setIsPremultipliedAlpha(enable) {
		this._isPremultipliedAlpha = enable;
	}
	/**
	* 乗算済みαの有効・無効を取得する
	* @return true 乗算済みのα有効
	*         false 乗算済みのα無効
	*/
	isPremultipliedAlpha() {
		return this._isPremultipliedAlpha;
	}
	/**
	* カリング（片面描画）の有効・無効をセットする。
	* 有効にするならtrue、無効にするならfalseをセットする
	*/
	setIsCulling(culling) {
		this._isCulling = culling;
	}
	/**
	* カリング（片面描画）の有効・無効を取得する。
	*
	* @return true カリング有効
	*         false カリング無効
	*/
	isCulling() {
		return this._isCulling;
	}
	/**
	* テクスチャの異方性フィルタリングのパラメータをセットする
	* パラメータ値の影響度はレンダラの実装に依存する
	*
	* @param n パラメータの値
	*/
	setAnisotropy(n) {
		this._anisotropy = n;
	}
	/**
	* テクスチャの異方性フィルタリングのパラメータをセットする
	*
	* @return 異方性フィルタリングのパラメータ
	*/
	getAnisotropy() {
		return this._anisotropy;
	}
	/**
	* レンダリングするモデルを取得する
	*
	* @return レンダリングするモデル
	*/
	getModel() {
		return this._model;
	}
	/**
	* マスク描画の方式を変更する。
	* falseの場合、マスクを1枚のテクスチャに分割してレンダリングする（デフォルト）
	* 高速だが、マスク個数の上限が36に限定され、質も荒くなる
	* trueの場合、パーツ描画の前にその都度必要なマスクを描き直す
	* レンダリング品質は高いが描画処理負荷は増す
	*
	* @param high 高精細マスクに切り替えるか？
	*/
	useHighPrecisionMask(high) {
		this._useHighPrecisionMask = high;
	}
	/**
	* マスクの描画方式を取得する
	*
	* @return true 高精細方式
	*         false デフォルト
	*/
	isUsingHighPrecisionMask() {
		return this._useHighPrecisionMask;
	}
	/**
	* モデルを描画したバッファのサイズを設定
	*
	* @param[in]   width  -> モデルを描画したバッファの幅
	* @param[in]   height -> モデルを描画したバッファの高さ
	*/
	setRenderTargetSize(width, height) {
		this._modelRenderTargetWidth = width;
		this._modelRenderTargetHeight = height;
	}
	/**
	* コンストラクタ
	*/
	constructor(width, height) {
		this._modelRenderTargetWidth = width;
		this._modelRenderTargetHeight = height;
		this._isCulling = false;
		this._isPremultipliedAlpha = false;
		this._anisotropy = 0;
		this._model = null;
		this._modelColor = new CubismTextureColor();
		this._useHighPrecisionMask = false;
		this._mvpMatrix4x4 = new CubismMatrix44();
		this._mvpMatrix4x4.loadIdentity();
	}
};
var CubismBlendMode = /* @__PURE__ */ function(CubismBlendMode) {
	CubismBlendMode[CubismBlendMode["CubismBlendMode_Normal"] = 0] = "CubismBlendMode_Normal";
	CubismBlendMode[CubismBlendMode["CubismBlendMode_Additive"] = 1] = "CubismBlendMode_Additive";
	CubismBlendMode[CubismBlendMode["CubismBlendMode_Multiplicative"] = 2] = "CubismBlendMode_Multiplicative";
	return CubismBlendMode;
}({});
/**
* オブジェクトのタイプ
*/
var DrawableObjectType = /* @__PURE__ */ function(DrawableObjectType) {
	DrawableObjectType[DrawableObjectType["DrawableObjectType_Drawable"] = 0] = "DrawableObjectType_Drawable";
	DrawableObjectType[DrawableObjectType["DrawableObjectType_Offscreen"] = 1] = "DrawableObjectType_Offscreen";
	return DrawableObjectType;
}({});
/**
* テクスチャの色をRGBAで扱うためのクラス
*/
var CubismTextureColor = class {
	/**
	* コンストラクタ
	*/
	constructor(r = 1, g = 1, b = 1, a = 1) {
		this.r = r;
		this.g = g;
		this.b = b;
		this.a = a;
	}
};
/**
* クリッピングマスクのコンテキスト
*/
var CubismClippingContext = class {
	/**
	* 引数付きコンストラクタ
	*/
	constructor(clippingDrawableIndices, clipCount) {
		this._clippingIdList = clippingDrawableIndices;
		this._clippingIdCount = clipCount;
		this._allClippedDrawRect = new csmRect();
		this._layoutBounds = new csmRect();
		this._clippedDrawableIndexList = [];
		this._clippedOffscreenIndexList = [];
		this._matrixForMask = new CubismMatrix44();
		this._matrixForDraw = new CubismMatrix44();
		this._bufferIndex = 0;
		this._layoutChannelIndex = 0;
	}
	/**
	* デストラクタ相当の処理
	*/
	release() {
		if (this._layoutBounds != null) this._layoutBounds = null;
		if (this._allClippedDrawRect != null) this._allClippedDrawRect = null;
		if (this._clippedDrawableIndexList != null) this._clippedDrawableIndexList = null;
		if (this._clippedOffscreenIndexList != null) this._clippedOffscreenIndexList = null;
	}
	/**
	* このマスクにクリップされる描画オブジェクトを追加する
	*
	* @param drawableIndex クリッピング対象に追加する描画オブジェクトのインデックス
	*/
	addClippedDrawable(drawableIndex) {
		this._clippedDrawableIndexList.push(drawableIndex);
	}
	/**
	* このマスクにクリップされるオフスクリーンオブジェクトを追加する
	*
	* @param offscreenIndex クリッピング対象に追加するオフスクリーンオブジェクトのインデックス
	*/
	addClippedOffscreen(offscreenIndex) {
		this._clippedOffscreenIndexList.push(offscreenIndex);
	}
};
var Live2DCubismFramework$29;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismBlendMode = CubismBlendMode;
	_Live2DCubismFramework.CubismRenderer = CubismRenderer;
	_Live2DCubismFramework.CubismTextureColor = CubismTextureColor;
})(Live2DCubismFramework$29 || (Live2DCubismFramework$29 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/utils/cubismjsonextension.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
/**
* CubismJsonで実装されているJsonパーサを使用せず、
* TypeScript標準のJsonパーサなどを使用し出力された結果を
* Cubism SDKで定義されているJSONエレメントの要素に
* 置き換える処理をするクラス。
*/
var CubismJsonExtension = class CubismJsonExtension {
	static parseJsonObject(obj, map) {
		Object.keys(obj).forEach((key) => {
			if (typeof obj[key] == "boolean") {
				const convValue = Boolean(obj[key]);
				map.put(key, new JsonBoolean(convValue));
			} else if (typeof obj[key] == "string") {
				const convValue = String(obj[key]);
				map.put(key, new JsonString(convValue));
			} else if (typeof obj[key] == "number") {
				const convValue = Number(obj[key]);
				map.put(key, new JsonFloat(convValue));
			} else if (obj[key] instanceof Array) map.put(key, CubismJsonExtension.parseJsonArray(obj[key]));
			else if (obj[key] instanceof Object) map.put(key, CubismJsonExtension.parseJsonObject(obj[key], new JsonMap()));
			else if (obj[key] == null) map.put(key, new JsonNullvalue());
			else map.put(key, obj[key]);
		});
		return map;
	}
	static parseJsonArray(obj) {
		const arr = new JsonArray();
		Object.keys(obj).forEach((key) => {
			if (typeof Number(key) == "number") {
				if (typeof obj[key] == "boolean") {
					const convValue = Boolean(obj[key]);
					arr.add(new JsonBoolean(convValue));
				} else if (typeof obj[key] == "string") {
					const convValue = String(obj[key]);
					arr.add(new JsonString(convValue));
				} else if (typeof obj[key] == "number") {
					const convValue = Number(obj[key]);
					arr.add(new JsonFloat(convValue));
				} else if (obj[key] instanceof Array) arr.add(this.parseJsonArray(obj[key]));
				else if (obj[key] instanceof Object) arr.add(this.parseJsonObject(obj[key], new JsonMap()));
				else if (obj[key] == null) arr.add(new JsonNullvalue());
				else arr.add(obj[key]);
			} else if (obj[key] instanceof Array) arr.add(this.parseJsonArray(obj[key]));
			else if (obj[key] instanceof Object) arr.add(this.parseJsonObject(obj[key], new JsonMap()));
			else if (obj[key] == null) arr.add(new JsonNullvalue());
			else {
				const convValue = Array(obj[key]);
				for (let i = 0; i < convValue.length; i++) arr.add(convValue[i]);
			}
		});
		return arr;
	}
};
// examples/packages/live2d_pet/vendor/Framework/src/utils/cubismjson.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
var CSM_JSON_ERROR_TYPE_MISMATCH = "Error: type mismatch";
var CSM_JSON_ERROR_INDEX_OF_BOUNDS = "Error: index out of bounds";
/**
* パースしたJSONエレメントの要素の基底クラス。
*/
var Value$2 = class Value$2 {
	/**
	* コンストラクタ
	*/
	constructor() {}
	/**
	* 要素を文字列型で返す(string)
	*/
	getRawString(defaultValue, indent) {
		return this.getString(defaultValue, indent);
	}
	/**
	* 要素を数値型で返す(number)
	*/
	toInt(defaultValue = 0) {
		return defaultValue;
	}
	/**
	* 要素を数値型で返す(number)
	*/
	toFloat(defaultValue = 0) {
		return defaultValue;
	}
	/**
	* 要素を真偽値で返す(boolean)
	*/
	toBoolean(defaultValue = false) {
		return defaultValue;
	}
	/**
	* サイズを返す
	*/
	getSize() {
		return 0;
	}
	/**
	* 要素を配列で返す(Value[])
	*/
	getArray(defaultValue = null) {
		return defaultValue;
	}
	/**
	* 要素をコンテナで返す(array)
	*/
	getVector(defaultValue = new Array()) {
		return defaultValue;
	}
	/**
	* 要素をマップで返す(Map<String, Value>)
	*/
	getMap(defaultValue) {
		return defaultValue;
	}
	/**
	* 添字演算子[index]
	*/
	getValueByIndex(index) {
		return Value$2.errorValue.setErrorNotForClientCall(CSM_JSON_ERROR_TYPE_MISMATCH);
	}
	/**
	* 添字演算子[string]
	*/
	getValueByString(s) {
		return Value$2.nullValue.setErrorNotForClientCall(CSM_JSON_ERROR_TYPE_MISMATCH);
	}
	/**
	* マップのキー一覧をコンテナで返す
	*
	* @return マップのキーの一覧
	*/
	getKeys() {
		return Value$2.dummyKeys;
	}
	/**
	* Valueの種類がエラー値ならtrue
	*/
	isError() {
		return false;
	}
	/**
	* Valueの種類がnullならtrue
	*/
	isNull() {
		return false;
	}
	/**
	* Valueの種類が真偽値ならtrue
	*/
	isBool() {
		return false;
	}
	/**
	* Valueの種類が数値型ならtrue
	*/
	isFloat() {
		return false;
	}
	/**
	* Valueの種類が文字列ならtrue
	*/
	isString() {
		return false;
	}
	/**
	* Valueの種類が配列ならtrue
	*/
	isArray() {
		return false;
	}
	/**
	* Valueの種類がマップ型ならtrue
	*/
	isMap() {
		return false;
	}
	equals(value) {
		return false;
	}
	/**
	* Valueの値が静的ならtrue、静的なら解放しない
	*/
	isStatic() {
		return false;
	}
	/**
	* Valueにエラー値をセットする
	*/
	setErrorNotForClientCall(errorStr) {
		return JsonError.errorValue;
	}
	/**
	* 初期化用メソッド
	*/
	static staticInitializeNotForClientCall() {
		JsonBoolean.trueValue = new JsonBoolean(true);
		JsonBoolean.falseValue = new JsonBoolean(false);
		Value$2.errorValue = new JsonError("ERROR", true);
		Value$2.nullValue = new JsonNullvalue();
		Value$2.dummyKeys = new Array();
	}
	/**
	* リリース用メソッド
	*/
	static staticReleaseNotForClientCall() {
		JsonBoolean.trueValue = null;
		JsonBoolean.falseValue = null;
		Value$2.errorValue = null;
		Value$2.nullValue = null;
		Value$2.dummyKeys = null;
	}
};
/**
* Ascii文字のみ対応した最小限の軽量JSONパーサ。
* 仕様はJSONのサブセットとなる。
* 設定ファイル(model3.json)などのロード用
*
* [未対応項目]
* ・日本語などの非ASCII文字
* ・eによる指数表現
*/
var CubismJson = class CubismJson {
	/**
	* コンストラクタ
	*/
	constructor(buffer, length) {
		this._parseCallback = CubismJsonExtension.parseJsonObject;
		this._error = null;
		this._lineCount = 0;
		this._root = null;
		if (buffer != void 0) this.parseBytes(buffer, length, this._parseCallback);
	}
	/**
	* バイトデータから直接ロードしてパースする
	*
	* @param buffer バッファ
	* @param size バッファサイズ
	* @return CubismJsonクラスのインスタンス。失敗したらNULL
	*/
	static create(buffer, size) {
		const json = new CubismJson();
		if (!json.parseBytes(buffer, size, json._parseCallback)) {
			CubismJson.delete(json);
			return null;
		} else return json;
	}
	/**
	* パースしたJSONオブジェクトの解放処理
	*
	* @param instance CubismJsonクラスのインスタンス
	*/
	static delete(instance) {
		instance = null;
	}
	/**
	* パースしたJSONのルート要素を返す
	*/
	getRoot() {
		return this._root;
	}
	/**
	*  UnicodeのバイナリをStringに変換
	*
	* @param buffer 変換するバイナリデータ
	* @return 変換後の文字列
	*/
	static arrayBufferToString(buffer) {
		const uint8Array = new Uint8Array(buffer);
		let str = "";
		for (let i = 0, len = uint8Array.length; i < len; ++i) str += "%" + this.pad(uint8Array[i].toString(16));
		str = decodeURIComponent(str);
		return str;
	}
	/**
	* エンコード、パディング
	*/
	static pad(n) {
		return n.length < 2 ? "0" + n : n;
	}
	/**
	* JSONのパースを実行する
	* @param buffer    パース対象のデータバイト
	* @param size      データバイトのサイズ
	* return true : 成功
	* return false: 失敗
	*/
	parseBytes(buffer, size, parseCallback) {
		const endPos = new Array(1);
		const decodeBuffer = CubismJson.arrayBufferToString(buffer);
		if (parseCallback == void 0) this._root = this.parseValue(decodeBuffer, size, 0, endPos);
		else this._root = parseCallback(JSON.parse(decodeBuffer), new JsonMap());
		if (this._error) {
			let strbuf = "\0";
			strbuf = "Json parse error : @line " + (this._lineCount + 1) + "\n";
			this._root = new JsonString(strbuf);
			CubismLogInfo("{0}", this._root.getRawString());
			return false;
		} else if (this._root == null) {
			this._root = new JsonError(this._error, false);
			return false;
		}
		return true;
	}
	/**
	* パース時のエラー値を返す
	*/
	getParseError() {
		return this._error;
	}
	/**
	* ルート要素の次の要素がファイルの終端だったらtrueを返す
	*/
	checkEndOfFile() {
		return this._root.getArray()[1].equals("EOF");
	}
	/**
	* JSONエレメントからValue(float,String,Value*,Array,null,true,false)をパースする
	* エレメントの書式に応じて内部でParseString(), ParseObject(), ParseArray()を呼ぶ
	*
	* @param   buffer      JSONエレメントのバッファ
	* @param   length      パースする長さ
	* @param   begin       パースを開始する位置
	* @param   outEndPos   パース終了時の位置
	* @return      パースから取得したValueオブジェクト
	*/
	parseValue(buffer, length, begin, outEndPos) {
		if (this._error) return null;
		let o = null;
		let i = begin;
		let f;
		for (; i < length; i++) switch (buffer[i]) {
			case "-":
			case ".":
			case "0":
			case "1":
			case "2":
			case "3":
			case "4":
			case "5":
			case "6":
			case "7":
			case "8":
			case "9": {
				const afterString = new Array(1);
				f = strtod(buffer.slice(i), afterString);
				outEndPos[0] = buffer.indexOf(afterString[0]);
				return new JsonFloat(f);
			}
			case "\"": return new JsonString(this.parseString(buffer, length, i + 1, outEndPos));
			case "[":
				o = this.parseArray(buffer, length, i + 1, outEndPos);
				return o;
			case "{":
				o = this.parseObject(buffer, length, i + 1, outEndPos);
				return o;
			case "n":
				if (i + 3 < length) {
					o = new JsonNullvalue();
					outEndPos[0] = i + 4;
				} else this._error = "parse null";
				return o;
			case "t":
				if (i + 3 < length) {
					o = JsonBoolean.trueValue;
					outEndPos[0] = i + 4;
				} else this._error = "parse true";
				return o;
			case "f":
				if (i + 4 < length) {
					o = JsonBoolean.falseValue;
					outEndPos[0] = i + 5;
				} else this._error = "illegal ',' position";
				return o;
			case ",":
				this._error = "illegal ',' position";
				return null;
			case "]":
				outEndPos[0] = i;
				return null;
			case "\n": this._lineCount++;
		}
		this._error = "illegal end of value";
		return null;
	}
	/**
	* 次の「"」までの文字列をパースする。
	*
	* @param   string  ->  パース対象の文字列
	* @param   length  ->  パースする長さ
	* @param   begin   ->  パースを開始する位置
	* @param  outEndPos   ->  パース終了時の位置
	* @return      パースした文F字列要素
	*/
	parseString(string, length, begin, outEndPos) {
		if (this._error) return null;
		if (!string) {
			this._error = "string is null";
			return null;
		}
		let i = begin;
		let c, c2;
		let ret = "";
		let bufStart = begin;
		for (; i < length; i++) {
			c = string[i];
			switch (c) {
				case "\"":
					outEndPos[0] = i + 1;
					ret += string.substr(bufStart, i - bufStart);
					return ret;
				case "//":
					i++;
					if (i - 1 > bufStart) ret += string.substr(bufStart, i - bufStart);
					bufStart = i + 1;
					if (i < length) {
						c2 = string[i];
						switch (c2) {
							case "\\":
								ret += "\\";
								break;
							case "\"":
								ret += "\"";
								break;
							case "/":
								ret += "/";
								break;
							case "b":
								ret += "\b";
								break;
							case "f":
								ret += "\f";
								break;
							case "n":
								ret += "\n";
								break;
							case "r":
								ret += "\r";
								break;
							case "t":
								ret += "	";
								break;
							case "u": this._error = "parse string/unicord escape not supported";
						}
					} else this._error = "parse string/escape error";
			}
		}
		this._error = "parse string/illegal end";
		return null;
	}
	/**
	* JSONのオブジェクトエレメントをパースしてValueオブジェクトを返す
	*
	* @param buffer    JSONエレメントのバッファ
	* @param length    パースする長さ
	* @param begin     パースを開始する位置
	* @param outEndPos パース終了時の位置
	* @return パースから取得したValueオブジェクト
	*/
	parseObject(buffer, length, begin, outEndPos) {
		if (this._error) return null;
		if (!buffer) {
			this._error = "buffer is null";
			return null;
		}
		const ret = new JsonMap();
		let key = "";
		let i = begin;
		let c = "";
		const localRetEndPos2 = Array(1);
		let ok = false;
		for (; i < length; i++) {
			FOR_LOOP: for (; i < length; i++) {
				c = buffer[i];
				switch (c) {
					case "\"":
						key = this.parseString(buffer, length, i + 1, localRetEndPos2);
						if (this._error) return null;
						i = localRetEndPos2[0];
						ok = true;
						break FOR_LOOP;
					case "}":
						outEndPos[0] = i + 1;
						return ret;
					case ":":
						this._error = "illegal ':' position";
						break;
					case "\n": this._lineCount++;
				}
			}
			if (!ok) {
				this._error = "key not found";
				return null;
			}
			ok = false;
			FOR_LOOP2: for (; i < length; i++) {
				c = buffer[i];
				switch (c) {
					case ":":
						ok = true;
						i++;
						break FOR_LOOP2;
					case "}":
						this._error = "illegal '}' position";
						break;
					case "\n": this._lineCount++;
				}
			}
			if (!ok) {
				this._error = "':' not found";
				return null;
			}
			const value = this.parseValue(buffer, length, i, localRetEndPos2);
			if (this._error) return null;
			i = localRetEndPos2[0];
			ret.put(key, value);
			FOR_LOOP3: for (; i < length; i++) {
				c = buffer[i];
				switch (c) {
					case ",": break FOR_LOOP3;
					case "}":
						outEndPos[0] = i + 1;
						return ret;
					case "\n": this._lineCount++;
				}
			}
		}
		this._error = "illegal end of perseObject";
		return null;
	}
	/**
	* 次の「"」までの文字列をパースする。
	* @param buffer    JSONエレメントのバッファ
	* @param length    パースする長さ
	* @param begin     パースを開始する位置
	* @param outEndPos パース終了時の位置
	* @return パースから取得したValueオブジェクト
	*/
	parseArray(buffer, length, begin, outEndPos) {
		if (this._error) return null;
		if (!buffer) {
			this._error = "buffer is null";
			return null;
		}
		let ret = new JsonArray();
		let i = begin;
		let c;
		const localRetEndpos2 = new Array(1);
		for (; i < length; i++) {
			const value = this.parseValue(buffer, length, i, localRetEndpos2);
			if (this._error) return null;
			i = localRetEndpos2[0];
			if (value) ret.add(value);
			FOR_LOOP: for (; i < length; i++) {
				c = buffer[i];
				switch (c) {
					case ",": break FOR_LOOP;
					case "]":
						outEndPos[0] = i + 1;
						return ret;
					case "\n": ++this._lineCount;
				}
			}
		}
		ret = void 0;
		this._error = "illegal end of parseObject";
		return null;
	}
};
/**
* パースしたJSONの要素をfloat値として扱う
*/
var JsonFloat = class extends Value$2 {
	/**
	* コンストラクタ
	*/
	constructor(v) {
		super();
		this._value = v;
	}
	/**
	* Valueの種類が数値型ならtrue
	*/
	isFloat() {
		return true;
	}
	/**
	* 要素を文字列で返す(string型)
	*/
	getString(defaultValue, indent) {
		const strbuf = "\0";
		this._value = parseFloat(strbuf);
		this._stringBuffer = strbuf;
		return this._stringBuffer;
	}
	/**
	* 要素を数値型で返す(number)
	*/
	toInt(defaultValue = 0) {
		return parseInt(this._value.toString());
	}
	/**
	* 要素を数値型で返す(number)
	*/
	toFloat(defaultValue = 0) {
		return this._value;
	}
	equals(value) {
		if ("number" === typeof value) {
			if (Math.round(value)) return false;
			else return value == this._value;
		}
		return false;
	}
};
/**
* パースしたJSONの要素を真偽値として扱う
*/
var JsonBoolean = class extends Value$2 {
	/**
	* Valueの種類が真偽値ならtrue
	*/
	isBool() {
		return true;
	}
	/**
	* 要素を真偽値で返す(boolean)
	*/
	toBoolean(defaultValue = false) {
		return this._boolValue;
	}
	/**
	* 要素を文字列で返す(string型)
	*/
	getString(defaultValue, indent) {
		this._stringBuffer = this._boolValue ? "true" : "false";
		return this._stringBuffer;
	}
	equals(value) {
		if ("boolean" === typeof value) return value == this._boolValue;
		return false;
	}
	/**
	* Valueの値が静的ならtrue, 静的なら解放しない
	*/
	isStatic() {
		return true;
	}
	/**
	* 引数付きコンストラクタ
	*/
	constructor(v) {
		super();
		this._boolValue = v;
	}
};
/**
* パースしたJSONの要素を文字列として扱う
*/
var JsonString = class extends Value$2 {
	/**
	* 引数付きコンストラクタ
	*/
	constructor(s) {
		super();
		this._stringBuffer = s;
	}
	/**
	* Valueの種類が文字列ならtrue
	*/
	isString() {
		return true;
	}
	/**
	* 要素を文字列で返す(string型)
	*/
	getString(defaultValue, indent) {
		return this._stringBuffer;
	}
	equals(value) {
		if ("string" === typeof value) return this._stringBuffer == value;
		return false;
	}
};
/**
* JSONパース時のエラー結果。文字列型のようにふるまう
*/
var JsonError = class extends JsonString {
	/**
	* Valueの値が静的ならtrue、静的なら解放しない
	*/
	isStatic() {
		return this._isStatic;
	}
	/**
	* エラー情報をセットする
	*/
	setErrorNotForClientCall(s) {
		this._stringBuffer = s;
		return this;
	}
	/**
	* 引数付きコンストラクタ
	*/
	constructor(s, isStatic) {
		if ("string" === typeof s) super(s);
		else super(s);
		this._isStatic = isStatic;
	}
	/**
	* Valueの種類がエラー値ならtrue
	*/
	isError() {
		return true;
	}
};
/**
* パースしたJSONの要素をNULL値として持つ
*/
var JsonNullvalue = class extends Value$2 {
	/**
	* Valueの種類がNULL値ならtrue
	*/
	isNull() {
		return true;
	}
	/**
	* 要素を文字列で返す(string型)
	*/
	getString(defaultValue, indent) {
		return this._stringBuffer;
	}
	/**
	* Valueの値が静的ならtrue, 静的なら解放しない
	*/
	isStatic() {
		return true;
	}
	/**
	* Valueにエラー値をセットする
	*/
	setErrorNotForClientCall(s) {
		this._stringBuffer = s;
		return JsonError.nullValue;
	}
	/**
	* コンストラクタ
	*/
	constructor() {
		super();
		this._stringBuffer = "NullValue";
	}
};
/**
* パースしたJSONの要素を配列として持つ
*/
var JsonArray = class extends Value$2 {
	/**
	* コンストラクタ
	*/
	constructor() {
		super();
		this._array = new Array();
	}
	/**
	* デストラクタ相当の処理
	*/
	release() {
		for (let i = 0; i < this._array.length; i++) {
			let v = this._array[i];
			if (v && !v.isStatic()) {
				v = void 0;
				v = null;
			}
		}
	}
	/**
	* Valueの種類が配列ならtrue
	*/
	isArray() {
		return true;
	}
	/**
	* 添字演算子[index]
	*/
	getValueByIndex(index) {
		if (index < 0 || this._array.length <= index) return Value$2.errorValue.setErrorNotForClientCall(CSM_JSON_ERROR_INDEX_OF_BOUNDS);
		const v = this._array[index];
		if (v == null) return Value$2.nullValue;
		return v;
	}
	/**
	* 添字演算子[string]
	*/
	getValueByString(s) {
		return Value$2.errorValue.setErrorNotForClientCall(CSM_JSON_ERROR_TYPE_MISMATCH);
	}
	/**
	* 要素を文字列で返す(string型)
	*/
	getString(defaultValue, indent) {
		const stringBuffer = indent + "[\n";
		for (let i = 0; i < this._array.length; i++) {
			const v = this._array[i];
			this._stringBuffer += indent + "" + v.getString(indent + " ") + "\n";
		}
		this._stringBuffer = stringBuffer + indent + "]\n";
		return this._stringBuffer;
	}
	/**
	* 配列要素を追加する
	* @param v 追加する要素
	*/
	add(v) {
		this._array.push(v);
	}
	/**
	* 要素をコンテナで返す(Array<Value>)
	*/
	getVector(defaultValue = null) {
		return this._array;
	}
	/**
	* 要素の数を返す
	*/
	getSize() {
		return this._array.length;
	}
};
/**
* パースしたJSONの要素をマップとして持つ
*/
var JsonMap = class extends Value$2 {
	/**
	* コンストラクタ
	*/
	constructor() {
		super();
		this._map = /* @__PURE__ */ new Map();
	}
	/**
	* デストラクタ相当の処理
	*/
	release() {
		this._map.clear();
	}
	/**
	* Valueの値がMap型ならtrue
	*/
	isMap() {
		return true;
	}
	/**
	* 添字演算子[string]
	*/
	getValueByString(s) {
		const ret = this._map.get(s);
		if (ret != void 0) return ret;
		return Value$2.nullValue;
	}
	/**
	* 添字演算子[index]
	*/
	getValueByIndex(index) {
		return Value$2.errorValue.setErrorNotForClientCall(CSM_JSON_ERROR_TYPE_MISMATCH);
	}
	/**
	* 要素を文字列で返す(string型)
	*/
	getString(defaultValue, indent) {
		this._stringBuffer = indent + "{\n";
		for (const element of this._map) {
			const key = element[0];
			const v = element[1];
			this._stringBuffer += indent + " " + key + " : " + v.getString(indent + "   ") + " \n";
		}
		this._stringBuffer += indent + "}\n";
		return this._stringBuffer;
	}
	/**
	* 要素をMap型で返す
	*/
	getMap(defaultValue) {
		return this._map;
	}
	/**
	* Mapに要素を追加する
	*/
	put(key, v) {
		this._map.set(key, v);
	}
	/**
	* Mapからキーのリストを取得する
	*/
	getKeys() {
		if (!this._keys) this._keys = [...this._map.keys()];
		return this._keys;
	}
	/**
	* Mapの要素数を取得する
	*/
	getSize() {
		return this._keys.length;
	}
};
var Live2DCubismFramework$28;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismJson = CubismJson;
	_Live2DCubismFramework.JsonArray = JsonArray;
	_Live2DCubismFramework.JsonBoolean = JsonBoolean;
	_Live2DCubismFramework.JsonError = JsonError;
	_Live2DCubismFramework.JsonFloat = JsonFloat;
	_Live2DCubismFramework.JsonMap = JsonMap;
	_Live2DCubismFramework.JsonNullvalue = JsonNullvalue;
	_Live2DCubismFramework.JsonString = JsonString;
	_Live2DCubismFramework.Value = Value$2;
})(Live2DCubismFramework$28 || (Live2DCubismFramework$28 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/live2dcubismframework.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
function strtod(s, endPtr) {
	let index = 0;
	for (let i = 1;; i++) {
		const testC = s.slice(i - 1, i);
		if (testC == "e" || testC == "-" || testC == "E") continue;
		const test = s.substring(0, i);
		const number = Number(test);
		if (isNaN(number)) break;
		index = i;
	}
	let d = parseFloat(s);
	if (isNaN(d)) d = NaN;
	endPtr[0] = s.slice(index);
	return d;
}
var s_isStarted = false;
var s_isInitialized = false;
var s_option = null;
var s_cubismIdManager = null;
/**
* Framework内で使う定数の宣言
*/
var Constant = Object.freeze({
	vertexOffset: 0,
	vertexStep: 2
});
function csmDelete(address) {
	if (!address) return;
	address = void 0;
}
/**
* Live2D Cubism SDK Original Workflow SDKのエントリポイント
* 利用開始時はCubismFramework.initialize()を呼び、CubismFramework.dispose()で終了する。
*/
var CubismFramework = class {
	/**
	* Cubism FrameworkのAPIを使用可能にする。
	*  APIを実行する前に必ずこの関数を実行すること。
	*  一度準備が完了して以降は、再び実行しても内部処理がスキップされます。
	*
	* @param    option      Optionクラスのインスタンス
	*
	* @return   準備処理が完了したらtrueが返ります。
	*/
	static startUp(option = null) {
		if (s_isStarted) {
			CubismLogInfo("CubismFramework.startUp() is already done.");
			return s_isStarted;
		}
		s_option = option;
		if (s_option != null) Live2DCubismCore.Logging.csmSetLogFunction(s_option.logFunction);
		s_isStarted = true;
		if (s_isStarted) {
			const version = Live2DCubismCore.Version.csmGetVersion();
			const major = (version & 4278190080) >> 24;
			const minor = (version & 16711680) >> 16;
			const patch = version & 65535;
			const versionNumber = version;
			CubismLogInfo(`Live2D Cubism Core version: {0}.{1}.{2} ({3})`, ("00" + major).slice(-2), ("00" + minor).slice(-2), ("0000" + patch).slice(-4), versionNumber);
		}
		CubismLogInfo("CubismFramework.startUp() is complete.");
		return s_isStarted;
	}
	/**
	* StartUp()で初期化したCubismFrameworkの各パラメータをクリアします。
	* Dispose()したCubismFrameworkを再利用する際に利用してください。
	*/
	static cleanUp() {
		s_isStarted = false;
		s_isInitialized = false;
		s_option = null;
		s_cubismIdManager = null;
	}
	/**
	* Cubism Framework内のリソースを初期化してモデルを表示可能な状態にします。<br>
	*     再度Initialize()するには先にDispose()を実行する必要があります。
	*
	* @param memorySize 初期化時メモリ量 [byte(s)]
	*    複数モデル表示時などにモデルが更新されない際に使用してください。
	*    指定する際は必ず1024*1024*16 byte(16MB)以上の値を指定してください。
	*    それ以外はすべて1024*1024*16 byteに丸めます。
	*/
	static initialize(memorySize = 0) {
		CSM_ASSERT(s_isStarted);
		if (!s_isStarted) {
			CubismLogWarning("CubismFramework is not started.");
			return;
		}
		if (s_isInitialized) {
			CubismLogWarning("CubismFramework.initialize() skipped, already initialized.");
			return;
		}
		Value$2.staticInitializeNotForClientCall();
		s_cubismIdManager = new CubismIdManager();
		Live2DCubismCore.Memory.initializeAmountOfMemory(memorySize);
		s_isInitialized = true;
		CubismLogInfo("CubismFramework.initialize() is complete.");
	}
	/**
	* Cubism Framework内の全てのリソースを解放します。
	*      ただし、外部で確保されたリソースについては解放しません。
	*      外部で適切に破棄する必要があります。
	*/
	static dispose() {
		CSM_ASSERT(s_isStarted);
		if (!s_isStarted) {
			CubismLogWarning("CubismFramework is not started.");
			return;
		}
		if (!s_isInitialized) {
			CubismLogWarning("CubismFramework.dispose() skipped, not initialized.");
			return;
		}
		Value$2.staticReleaseNotForClientCall();
		s_cubismIdManager.release();
		s_cubismIdManager = null;
		CubismRenderer.staticRelease();
		s_isInitialized = false;
		CubismLogInfo("CubismFramework.dispose() is complete.");
	}
	/**
	* Cubism FrameworkのAPIを使用する準備が完了したかどうか
	* @return APIを使用する準備が完了していればtrueが返ります。
	*/
	static isStarted() {
		return s_isStarted;
	}
	/**
	* Cubism Frameworkのリソース初期化がすでに行われているかどうか
	* @return リソース確保が完了していればtrueが返ります
	*/
	static isInitialized() {
		return s_isInitialized;
	}
	/**
	* Core APIにバインドしたログ関数を実行する
	*
	* @praram message ログメッセージ
	*/
	static coreLogFunction(message) {
		if (!Live2DCubismCore.Logging.csmGetLogFunction()) return;
		Live2DCubismCore.Logging.csmGetLogFunction()(message);
	}
	/**
	* 現在のログ出力レベル設定の値を返す。
	*
	* @return  現在のログ出力レベル設定の値
	*/
	static getLoggingLevel() {
		if (s_option != null) return s_option.loggingLevel;
		return 5;
	}
	/**
	* IDマネージャのインスタンスを取得する
	* @return CubismManagerクラスのインスタンス
	*/
	static getIdManager() {
		return s_cubismIdManager;
	}
	/**
	* 静的クラスとして使用する
	* インスタンス化させない
	*/
	constructor() {}
};
/**
* ログ出力のレベル
*/
var LogLevel = /* @__PURE__ */ function(LogLevel) {
	LogLevel[LogLevel["LogLevel_Verbose"] = 0] = "LogLevel_Verbose";
	LogLevel[LogLevel["LogLevel_Debug"] = 1] = "LogLevel_Debug";
	LogLevel[LogLevel["LogLevel_Info"] = 2] = "LogLevel_Info";
	LogLevel[LogLevel["LogLevel_Warning"] = 3] = "LogLevel_Warning";
	LogLevel[LogLevel["LogLevel_Error"] = 4] = "LogLevel_Error";
	LogLevel[LogLevel["LogLevel_Off"] = 5] = "LogLevel_Off";
	return LogLevel;
}({});
var Live2DCubismFramework$27;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.Constant = Constant;
	_Live2DCubismFramework.csmDelete = csmDelete;
	_Live2DCubismFramework.CubismFramework = CubismFramework;
})(Live2DCubismFramework$27 || (Live2DCubismFramework$27 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/icubismmodelsetting.ts
/**
* モデル設定情報を取り扱う関数を宣言した純粋仮想クラス。
*
* このクラスを継承することで、モデル設定情報を取り扱うクラスになる。
*/
var ICubismModelSetting = class {};
var Live2DCubismFramework$26;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.ICubismModelSetting = ICubismModelSetting;
})(Live2DCubismFramework$26 || (Live2DCubismFramework$26 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/cubismmodelsettingjson.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
var FrequestNode = /* @__PURE__ */ function(FrequestNode) {
	FrequestNode[FrequestNode["FrequestNode_Groups"] = 0] = "FrequestNode_Groups";
	FrequestNode[FrequestNode["FrequestNode_Moc"] = 1] = "FrequestNode_Moc";
	FrequestNode[FrequestNode["FrequestNode_Motions"] = 2] = "FrequestNode_Motions";
	FrequestNode[FrequestNode["FrequestNode_Expressions"] = 3] = "FrequestNode_Expressions";
	FrequestNode[FrequestNode["FrequestNode_Textures"] = 4] = "FrequestNode_Textures";
	FrequestNode[FrequestNode["FrequestNode_Physics"] = 5] = "FrequestNode_Physics";
	FrequestNode[FrequestNode["FrequestNode_Pose"] = 6] = "FrequestNode_Pose";
	FrequestNode[FrequestNode["FrequestNode_HitAreas"] = 7] = "FrequestNode_HitAreas";
	return FrequestNode;
}({});
/**
* Model3Jsonパーサー
*
* model3.jsonファイルをパースして値を取得する
*/
var CubismModelSettingJson = class extends ICubismModelSetting {
	/**
	* 引数付きコンストラクタ
	*
	* @param buffer    Model3Jsonをバイト配列として読み込んだデータバッファ
	* @param size      Model3Jsonのデータサイズ
	*/
	constructor(buffer, size) {
		super();
		this.version = "Version";
		this.fileReferences = "FileReferences";
		this.groups = "Groups";
		this.layout = "Layout";
		this.hitAreas = "HitAreas";
		this.moc = "Moc";
		this.textures = "Textures";
		this.physics = "Physics";
		this.pose = "Pose";
		this.expressions = "Expressions";
		this.motions = "Motions";
		this.userData = "UserData";
		this.name = "Name";
		this.filePath = "File";
		this.id = "Id";
		this.ids = "Ids";
		this.target = "Target";
		this.idle = "Idle";
		this.tapBody = "TapBody";
		this.pinchIn = "PinchIn";
		this.pinchOut = "PinchOut";
		this.shake = "Shake";
		this.flickHead = "FlickHead";
		this.parameter = "Parameter";
		this.soundPath = "Sound";
		this.fadeInTime = "FadeInTime";
		this.fadeOutTime = "FadeOutTime";
		this.centerX = "CenterX";
		this.centerY = "CenterY";
		this.x = "X";
		this.y = "Y";
		this.width = "Width";
		this.height = "Height";
		this.lipSync = "LipSync";
		this.eyeBlink = "EyeBlink";
		this.initParameter = "init_param";
		this.initPartsVisible = "init_parts_visible";
		this.val = "val";
		this._json = CubismJson.create(buffer, size);
		if (this.getJson()) this._jsonValue = [
			this.getJson().getRoot().getValueByString(this.groups),
			this.getJson().getRoot().getValueByString(this.fileReferences).getValueByString(this.moc),
			this.getJson().getRoot().getValueByString(this.fileReferences).getValueByString(this.motions),
			this.getJson().getRoot().getValueByString(this.fileReferences).getValueByString(this.expressions),
			this.getJson().getRoot().getValueByString(this.fileReferences).getValueByString(this.textures),
			this.getJson().getRoot().getValueByString(this.fileReferences).getValueByString(this.physics),
			this.getJson().getRoot().getValueByString(this.fileReferences).getValueByString(this.pose),
			this.getJson().getRoot().getValueByString(this.hitAreas)
		];
	}
	/**
	* デストラクタ相当の処理
	*/
	release() {
		CubismJson.delete(this._json);
		this._jsonValue = null;
	}
	/**
	* CubismJsonオブジェクトを取得する
	*
	* @return CubismJson
	*/
	getJson() {
		return this._json;
	}
	/**
	* Mocファイルの名前を取得する
	* @return Mocファイルの名前
	*/
	getModelFileName() {
		if (!this.isExistModelFile()) return "";
		return this._jsonValue[1].getRawString();
	}
	/**
	* モデルが使用するテクスチャの数を取得する
	* テクスチャの数
	*/
	getTextureCount() {
		if (!this.isExistTextureFiles()) return 0;
		return this._jsonValue[4].getSize();
	}
	/**
	* テクスチャが配置されたディレクトリの名前を取得する
	* @return テクスチャが配置されたディレクトリの名前
	*/
	getTextureDirectory() {
		const pathArray = this._jsonValue[4].getValueByIndex(0).getRawString().split("/");
		const arrayLength = pathArray.length - 1;
		let textureDirectoryStr = "";
		for (let i = 0; i < arrayLength; i++) {
			textureDirectoryStr += pathArray[i];
			if (i < arrayLength - 1) textureDirectoryStr += "/";
		}
		return textureDirectoryStr;
	}
	/**
	* モデルが使用するテクスチャの名前を取得する
	* @param index 配列のインデックス値
	* @return テクスチャの名前
	*/
	getTextureFileName(index) {
		return this._jsonValue[4].getValueByIndex(index).getRawString();
	}
	/**
	* モデルに設定された当たり判定の数を取得する
	* @return モデルに設定された当たり判定の数
	*/
	getHitAreasCount() {
		if (!this.isExistHitAreas()) return 0;
		return this._jsonValue[7].getSize();
	}
	/**
	* 当たり判定に設定されたIDを取得する
	*
	* @param index 配列のindex
	* @return 当たり判定に設定されたID
	*/
	getHitAreaId(index) {
		return CubismFramework.getIdManager().getId(this._jsonValue[7].getValueByIndex(index).getValueByString(this.id).getRawString());
	}
	/**
	* 当たり判定に設定された名前を取得する
	* @param index 配列のインデックス値
	* @return 当たり判定に設定された名前
	*/
	getHitAreaName(index) {
		return this._jsonValue[7].getValueByIndex(index).getValueByString(this.name).getRawString();
	}
	/**
	* 物理演算設定ファイルの名前を取得する
	* @return 物理演算設定ファイルの名前
	*/
	getPhysicsFileName() {
		if (!this.isExistPhysicsFile()) return "";
		return this._jsonValue[5].getRawString();
	}
	/**
	* パーツ切り替え設定ファイルの名前を取得する
	* @return パーツ切り替え設定ファイルの名前
	*/
	getPoseFileName() {
		if (!this.isExistPoseFile()) return "";
		return this._jsonValue[6].getRawString();
	}
	/**
	* 表情設定ファイルの数を取得する
	* @return 表情設定ファイルの数
	*/
	getExpressionCount() {
		if (!this.isExistExpressionFile()) return 0;
		return this._jsonValue[3].getSize();
	}
	/**
	* 表情設定ファイルを識別する名前（別名）を取得する
	* @param index 配列のインデックス値
	* @return 表情の名前
	*/
	getExpressionName(index) {
		return this._jsonValue[3].getValueByIndex(index).getValueByString(this.name).getRawString();
	}
	/**
	* 表情設定ファイルの名前を取得する
	* @param index 配列のインデックス値
	* @return 表情設定ファイルの名前
	*/
	getExpressionFileName(index) {
		return this._jsonValue[3].getValueByIndex(index).getValueByString(this.filePath).getRawString();
	}
	/**
	* モーショングループの数を取得する
	* @return モーショングループの数
	*/
	getMotionGroupCount() {
		if (!this.isExistMotionGroups()) return 0;
		return this._jsonValue[2].getKeys().length;
	}
	/**
	* モーショングループの名前を取得する
	* @param index 配列のインデックス値
	* @return モーショングループの名前
	*/
	getMotionGroupName(index) {
		if (!this.isExistMotionGroups()) return null;
		return this._jsonValue[2].getKeys()[index];
	}
	/**
	* モーショングループに含まれるモーションの数を取得する
	* @param groupName モーショングループの名前
	* @return モーショングループの数
	*/
	getMotionCount(groupName) {
		if (!this.isExistMotionGroupName(groupName)) return 0;
		return this._jsonValue[2].getValueByString(groupName).getSize();
	}
	/**
	* グループ名とインデックス値からモーションファイル名を取得する
	* @param groupName モーショングループの名前
	* @param index     配列のインデックス値
	* @return モーションファイルの名前
	*/
	getMotionFileName(groupName, index) {
		if (!this.isExistMotionGroupName(groupName)) return "";
		return this._jsonValue[2].getValueByString(groupName).getValueByIndex(index).getValueByString(this.filePath).getRawString();
	}
	/**
	* モーションに対応するサウンドファイルの名前を取得する
	* @param groupName モーショングループの名前
	* @param index 配列のインデックス値
	* @return サウンドファイルの名前
	*/
	getMotionSoundFileName(groupName, index) {
		if (!this.isExistMotionSoundFile(groupName, index)) return "";
		return this._jsonValue[2].getValueByString(groupName).getValueByIndex(index).getValueByString(this.soundPath).getRawString();
	}
	/**
	* モーション開始時のフェードイン処理時間を取得する
	* @param groupName モーショングループの名前
	* @param index 配列のインデックス値
	* @return フェードイン処理時間[秒]
	*/
	getMotionFadeInTimeValue(groupName, index) {
		if (!this.isExistMotionFadeIn(groupName, index)) return -1;
		return this._jsonValue[2].getValueByString(groupName).getValueByIndex(index).getValueByString(this.fadeInTime).toFloat();
	}
	/**
	* モーション終了時のフェードアウト処理時間を取得する
	* @param groupName モーショングループの名前
	* @param index 配列のインデックス値
	* @return フェードアウト処理時間[秒]
	*/
	getMotionFadeOutTimeValue(groupName, index) {
		if (!this.isExistMotionFadeOut(groupName, index)) return -1;
		return this._jsonValue[2].getValueByString(groupName).getValueByIndex(index).getValueByString(this.fadeOutTime).toFloat();
	}
	/**
	* ユーザーデータのファイル名を取得する
	* @return ユーザーデータのファイル名
	*/
	getUserDataFile() {
		if (!this.isExistUserDataFile()) return "";
		return this.getJson().getRoot().getValueByString(this.fileReferences).getValueByString(this.userData).getRawString();
	}
	/**
	* レイアウト情報を取得する
	* @param outLayoutMap Mapクラスのインスタンス
	* @return true レイアウト情報が存在する
	* @return false レイアウト情報が存在しない
	*/
	getLayoutMap(outLayoutMap) {
		const map = this.getJson().getRoot().getValueByString(this.layout).getMap();
		if (map == null) return false;
		let ret = false;
		for (const element of map) {
			outLayoutMap.set(element[0], element[1].toFloat());
			ret = true;
		}
		return ret;
	}
	/**
	* 目パチに関連付けられたパラメータの数を取得する
	* @return 目パチに関連付けられたパラメータの数
	*/
	getEyeBlinkParameterCount() {
		if (!this.isExistEyeBlinkParameters()) return 0;
		let num = 0;
		for (let i = 0; i < this._jsonValue[0].getSize(); i++) {
			const refI = this._jsonValue[0].getValueByIndex(i);
			if (refI.isNull() || refI.isError()) continue;
			if (refI.getValueByString(this.name).getRawString() == this.eyeBlink) {
				num = refI.getValueByString(this.ids).getVector().length;
				break;
			}
		}
		return num;
	}
	/**
	* 目パチに関連付けられたパラメータのIDを取得する
	* @param index 配列のインデックス値
	* @return パラメータID
	*/
	getEyeBlinkParameterId(index) {
		if (!this.isExistEyeBlinkParameters()) return null;
		for (let i = 0; i < this._jsonValue[0].getSize(); i++) {
			const refI = this._jsonValue[0].getValueByIndex(i);
			if (refI.isNull() || refI.isError()) continue;
			if (refI.getValueByString(this.name).getRawString() == this.eyeBlink) return CubismFramework.getIdManager().getId(refI.getValueByString(this.ids).getValueByIndex(index).getRawString());
		}
		return null;
	}
	/**
	* リップシンクに関連付けられたパラメータの数を取得する
	* @return リップシンクに関連付けられたパラメータの数
	*/
	getLipSyncParameterCount() {
		if (!this.isExistLipSyncParameters()) return 0;
		let num = 0;
		for (let i = 0; i < this._jsonValue[0].getSize(); i++) {
			const refI = this._jsonValue[0].getValueByIndex(i);
			if (refI.isNull() || refI.isError()) continue;
			if (refI.getValueByString(this.name).getRawString() == this.lipSync) {
				num = refI.getValueByString(this.ids).getVector().length;
				break;
			}
		}
		return num;
	}
	/**
	* リップシンクに関連付けられたパラメータの数を取得する
	* @param index 配列のインデックス値
	* @return パラメータID
	*/
	getLipSyncParameterId(index) {
		if (!this.isExistLipSyncParameters()) return null;
		for (let i = 0; i < this._jsonValue[0].getSize(); i++) {
			const refI = this._jsonValue[0].getValueByIndex(i);
			if (refI.isNull() || refI.isError()) continue;
			if (refI.getValueByString(this.name).getRawString() == this.lipSync) return CubismFramework.getIdManager().getId(refI.getValueByString(this.ids).getValueByIndex(index).getRawString());
		}
		return null;
	}
	/**
	* モデルファイルのキーが存在するかどうかを確認する
	* @return true キーが存在する
	* @return false キーが存在しない
	*/
	isExistModelFile() {
		const node = this._jsonValue[1];
		return !node.isNull() && !node.isError();
	}
	/**
	* テクスチャファイルのキーが存在するかどうかを確認する
	* @return true キーが存在する
	* @return false キーが存在しない
	*/
	isExistTextureFiles() {
		const node = this._jsonValue[4];
		return !node.isNull() && !node.isError();
	}
	/**
	* 当たり判定のキーが存在するかどうかを確認する
	* @return true キーが存在する
	* @return false キーが存在しない
	*/
	isExistHitAreas() {
		const node = this._jsonValue[7];
		return !node.isNull() && !node.isError();
	}
	/**
	* 物理演算ファイルのキーが存在するかどうかを確認する
	* @return true キーが存在する
	* @return false キーが存在しない
	*/
	isExistPhysicsFile() {
		const node = this._jsonValue[5];
		return !node.isNull() && !node.isError();
	}
	/**
	* ポーズ設定ファイルのキーが存在するかどうかを確認する
	* @return true キーが存在する
	* @return false キーが存在しない
	*/
	isExistPoseFile() {
		const node = this._jsonValue[6];
		return !node.isNull() && !node.isError();
	}
	/**
	* 表情設定ファイルのキーが存在するかどうかを確認する
	* @return true キーが存在する
	* @return false キーが存在しない
	*/
	isExistExpressionFile() {
		const node = this._jsonValue[3];
		return !node.isNull() && !node.isError();
	}
	/**
	* モーショングループのキーが存在するかどうかを確認する
	* @return true キーが存在する
	* @return false キーが存在しない
	*/
	isExistMotionGroups() {
		const node = this._jsonValue[2];
		return !node.isNull() && !node.isError();
	}
	/**
	* 引数で指定したモーショングループのキーが存在するかどうかを確認する
	* @param groupName  グループ名
	* @return true キーが存在する
	* @return false キーが存在しない
	*/
	isExistMotionGroupName(groupName) {
		const node = this._jsonValue[2].getValueByString(groupName);
		return !node.isNull() && !node.isError();
	}
	/**
	* 引数で指定したモーションに対応するサウンドファイルのキーが存在するかどうかを確認する
	* @param groupName  グループ名
	* @param index 配列のインデックス値
	* @return true キーが存在する
	* @return false キーが存在しない
	*/
	isExistMotionSoundFile(groupName, index) {
		const node = this._jsonValue[2].getValueByString(groupName).getValueByIndex(index).getValueByString(this.soundPath);
		return !node.isNull() && !node.isError();
	}
	/**
	* 引数で指定したモーションに対応するフェードイン時間のキーが存在するかどうかを確認する
	* @param groupName  グループ名
	* @param index 配列のインデックス値
	* @return true キーが存在する
	* @return false キーが存在しない
	*/
	isExistMotionFadeIn(groupName, index) {
		const node = this._jsonValue[2].getValueByString(groupName).getValueByIndex(index).getValueByString(this.fadeInTime);
		return !node.isNull() && !node.isError();
	}
	/**
	* 引数で指定したモーションに対応するフェードアウト時間のキーが存在するかどうかを確認する
	* @param groupName  グループ名
	* @param index 配列のインデックス値
	* @return true キーが存在する
	* @return false キーが存在しない
	*/
	isExistMotionFadeOut(groupName, index) {
		const node = this._jsonValue[2].getValueByString(groupName).getValueByIndex(index).getValueByString(this.fadeOutTime);
		return !node.isNull() && !node.isError();
	}
	/**
	* UserDataのファイル名が存在するかどうかを確認する
	* @return true キーが存在する
	* @return false キーが存在しない
	*/
	isExistUserDataFile() {
		const node = this.getJson().getRoot().getValueByString(this.fileReferences).getValueByString(this.userData);
		return !node.isNull() && !node.isError();
	}
	/**
	* 目ぱちに対応付けられたパラメータが存在するかどうかを確認する
	* @return true キーが存在する
	* @return false キーが存在しない
	*/
	isExistEyeBlinkParameters() {
		if (this._jsonValue[0].isNull() || this._jsonValue[0].isError()) return false;
		for (let i = 0; i < this._jsonValue[0].getSize(); ++i) if (this._jsonValue[0].getValueByIndex(i).getValueByString(this.name).getRawString() == this.eyeBlink) return true;
		return false;
	}
	/**
	* リップシンクに対応付けられたパラメータが存在するかどうかを確認する
	* @return true キーが存在する
	* @return false キーが存在しない
	*/
	isExistLipSyncParameters() {
		if (this._jsonValue[0].isNull() || this._jsonValue[0].isError()) return false;
		for (let i = 0; i < this._jsonValue[0].getSize(); ++i) if (this._jsonValue[0].getValueByIndex(i).getValueByString(this.name).getRawString() == this.lipSync) return true;
		return false;
	}
};
var Live2DCubismFramework$25;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismModelSettingJson = CubismModelSettingJson;
	_Live2DCubismFramework.FrequestNode = FrequestNode;
})(Live2DCubismFramework$25 || (Live2DCubismFramework$25 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/effect/cubismbreath.ts
/**
* 呼吸機能
*
* 呼吸機能を提供する。
*/
var CubismBreath = class CubismBreath {
	/**
	* インスタンスの作成
	*/
	static create() {
		return new CubismBreath();
	}
	/**
	* インスタンスの破棄
	* @param instance 対象のCubismBreath
	*/
	static delete(instance) {
		if (instance != null) instance = null;
	}
	/**
	* 呼吸のパラメータの紐づけ
	* @param breathParameters 呼吸を紐づけたいパラメータのリスト
	*/
	setParameters(breathParameters) {
		this._breathParameters = breathParameters;
	}
	/**
	* 呼吸に紐づいているパラメータの取得
	* @return 呼吸に紐づいているパラメータのリスト
	*/
	getParameters() {
		return this._breathParameters;
	}
	/**
	* モデルのパラメータの更新
	* @param model 対象のモデル
	* @param deltaTimeSeconds デルタ時間[秒]
	*/
	updateParameters(model, deltaTimeSeconds) {
		this._currentTime += deltaTimeSeconds;
		const t = this._currentTime * 2 * Math.PI;
		for (let i = 0; i < this._breathParameters.length; ++i) {
			const data = this._breathParameters[i];
			model.addParameterValueById(data.parameterId, data.offset + data.peak * Math.sin(t / data.cycle), data.weight);
		}
	}
	/**
	* コンストラクタ
	*/
	constructor() {
		this._currentTime = 0;
	}
};
/**
* 呼吸のパラメータ情報
*/
var BreathParameterData = class {
	/**
	* コンストラクタ
	* @param parameterId   呼吸をひもづけるパラメータID
	* @param offset        呼吸を正弦波としたときの、波のオフセット
	* @param peak          呼吸を正弦波としたときの、波の高さ
	* @param cycle         呼吸を正弦波としたときの、波の周期
	* @param weight        パラメータへの重み
	*/
	constructor(parameterId, offset, peak, cycle, weight) {
		this.parameterId = parameterId == void 0 ? null : parameterId;
		this.offset = offset == void 0 ? 0 : offset;
		this.peak = peak == void 0 ? 0 : peak;
		this.cycle = cycle == void 0 ? 0 : cycle;
		this.weight = weight == void 0 ? 0 : weight;
	}
};
var Live2DCubismFramework$24;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.BreathParameterData = BreathParameterData;
	_Live2DCubismFramework.CubismBreath = CubismBreath;
})(Live2DCubismFramework$24 || (Live2DCubismFramework$24 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/effect/cubismeyeblink.ts
/**
* 自動まばたき機能
*
* 自動まばたき機能を提供する。
*/
var CubismEyeBlink = class CubismEyeBlink {
	/**
	* インスタンスを作成する
	* @param modelSetting モデルの設定情報
	* @return 作成されたインスタンス
	* @note 引数がNULLの場合、パラメータIDが設定されていない空のインスタンスを作成する。
	*/
	static create(modelSetting = null) {
		return new CubismEyeBlink(modelSetting);
	}
	/**
	* インスタンスの破棄
	* @param eyeBlink 対象のCubismEyeBlink
	*/
	static delete(eyeBlink) {
		if (eyeBlink != null) eyeBlink = null;
	}
	/**
	* まばたきの間隔の設定
	* @param blinkingInterval まばたきの間隔の時間[秒]
	*/
	setBlinkingInterval(blinkingInterval) {
		this._blinkingIntervalSeconds = blinkingInterval;
	}
	/**
	* まばたきのモーションの詳細設定
	* @param closing   まぶたを閉じる動作の所要時間[秒]
	* @param closed    まぶたを閉じている動作の所要時間[秒]
	* @param opening   まぶたを開く動作の所要時間[秒]
	*/
	setBlinkingSetting(closing, closed, opening) {
		this._closingSeconds = closing;
		this._closedSeconds = closed;
		this._openingSeconds = opening;
	}
	/**
	* まばたきさせるパラメータIDのリストの設定
	* @param parameterIds パラメータのIDのリスト
	*/
	setParameterIds(parameterIds) {
		this._parameterIds = parameterIds;
	}
	/**
	* まばたきさせるパラメータIDのリストの取得
	* @return パラメータIDのリスト
	*/
	getParameterIds() {
		return this._parameterIds;
	}
	/**
	* モデルのパラメータの更新
	* @param model 対象のモデル
	* @param deltaTimeSeconds デルタ時間[秒]
	*/
	updateParameters(model, deltaTimeSeconds) {
		this._userTimeSeconds += deltaTimeSeconds;
		let parameterValue;
		let t = 0;
		switch (this._blinkingState) {
			case 2:
				t = (this._userTimeSeconds - this._stateStartTimeSeconds) / this._closingSeconds;
				if (t >= 1) {
					t = 1;
					this._blinkingState = 3;
					this._stateStartTimeSeconds = this._userTimeSeconds;
				}
				parameterValue = 1 - t;
				break;
			case 3:
				t = (this._userTimeSeconds - this._stateStartTimeSeconds) / this._closedSeconds;
				if (t >= 1) {
					this._blinkingState = 4;
					this._stateStartTimeSeconds = this._userTimeSeconds;
				}
				parameterValue = 0;
				break;
			case 4:
				t = (this._userTimeSeconds - this._stateStartTimeSeconds) / this._openingSeconds;
				if (t >= 1) {
					t = 1;
					this._blinkingState = 1;
					this._nextBlinkingTime = this.determinNextBlinkingTiming();
				}
				parameterValue = t;
				break;
			case 1:
				if (this._nextBlinkingTime < this._userTimeSeconds) {
					this._blinkingState = 2;
					this._stateStartTimeSeconds = this._userTimeSeconds;
				}
				parameterValue = 1;
				break;
			default:
				this._blinkingState = 1;
				this._nextBlinkingTime = this.determinNextBlinkingTiming();
				parameterValue = 1;
		}
		if (!CubismEyeBlink.CloseIfZero) parameterValue = -parameterValue;
		for (let i = 0; i < this._parameterIds.length; ++i) model.setParameterValueById(this._parameterIds[i], parameterValue);
	}
	/**
	* コンストラクタ
	* @param modelSetting モデルの設定情報
	*/
	constructor(modelSetting) {
		this._blinkingState = 0;
		this._nextBlinkingTime = 0;
		this._stateStartTimeSeconds = 0;
		this._blinkingIntervalSeconds = 4;
		this._closingSeconds = .1;
		this._closedSeconds = .05;
		this._openingSeconds = .15;
		this._userTimeSeconds = 0;
		this._parameterIds = new Array();
		if (modelSetting == null) return;
		this._parameterIds.length = modelSetting.getEyeBlinkParameterCount();
		for (let i = 0; i < modelSetting.getEyeBlinkParameterCount(); ++i) this._parameterIds[i] = modelSetting.getEyeBlinkParameterId(i);
	}
	/**
	* 次の瞬きのタイミングの決定
	*
	* @return 次のまばたきを行う時刻[秒]
	*/
	determinNextBlinkingTiming() {
		const r = Math.random();
		return this._userTimeSeconds + r * (2 * this._blinkingIntervalSeconds - 1);
	}
	static {
		this.CloseIfZero = true;
	}
};
/**
* まばたきの状態
*
* まばたきの状態を表す列挙型
*/
var EyeState = /* @__PURE__ */ function(EyeState) {
	EyeState[EyeState["EyeState_First"] = 0] = "EyeState_First";
	EyeState[EyeState["EyeState_Interval"] = 1] = "EyeState_Interval";
	EyeState[EyeState["EyeState_Closing"] = 2] = "EyeState_Closing";
	EyeState[EyeState["EyeState_Closed"] = 3] = "EyeState_Closed";
	EyeState[EyeState["EyeState_Opening"] = 4] = "EyeState_Opening";
	return EyeState;
}({});
var Live2DCubismFramework$23;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismEyeBlink = CubismEyeBlink;
	_Live2DCubismFramework.EyeState = EyeState;
})(Live2DCubismFramework$23 || (Live2DCubismFramework$23 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/effect/cubismpose.ts
var Epsilon$1 = .001;
var DefaultFadeInSeconds = .5;
var FadeIn = "FadeInTime";
var Link = "Link";
var Groups = "Groups";
var Id$3 = "Id";
/**
* パーツの不透明度の設定
*
* パーツの不透明度の管理と設定を行う。
*/
var CubismPose = class CubismPose {
	/**
	* インスタンスの作成
	* @param pose3json pose3.jsonのデータ
	* @param size pose3.jsonのデータのサイズ[byte]
	* @return 作成されたインスタンス
	*/
	static create(pose3json, size) {
		const json = CubismJson.create(pose3json, size);
		if (!json) return null;
		const ret = new CubismPose();
		const root = json.getRoot();
		if (!root.getValueByString(FadeIn).isNull()) {
			ret._fadeTimeSeconds = root.getValueByString(FadeIn).toFloat(DefaultFadeInSeconds);
			if (ret._fadeTimeSeconds < 0) ret._fadeTimeSeconds = DefaultFadeInSeconds;
		}
		const poseListInfo = root.getValueByString(Groups);
		const poseCount = poseListInfo.getSize();
		ret._partGroupCounts.length = poseCount;
		for (let poseIndex = 0; poseIndex < poseCount; ++poseIndex) {
			const idListInfo = poseListInfo.getValueByIndex(poseIndex);
			const idCount = idListInfo.getSize();
			let groupCount = 0;
			for (let groupIndex = 0; groupIndex < idCount; ++groupIndex) {
				const partInfo = idListInfo.getValueByIndex(groupIndex);
				const partData = new PartData();
				partData.partId = CubismFramework.getIdManager().getId(partInfo.getValueByString(Id$3).getRawString());
				if (!partInfo.getValueByString(Link).isNull()) {
					const linkListInfo = partInfo.getValueByString(Link);
					const linkCount = linkListInfo.getSize();
					for (let linkIndex = 0; linkIndex < linkCount; ++linkIndex) {
						const linkPart = new PartData();
						linkPart.partId = CubismFramework.getIdManager().getId(linkListInfo.getValueByIndex(linkIndex).getString());
						partData.link.push(linkPart);
					}
				}
				ret._partGroups.push(partData.clone());
				++groupCount;
			}
			ret._partGroupCounts[poseIndex] = groupCount;
		}
		CubismJson.delete(json);
		return ret;
	}
	/**
	* インスタンスを破棄する
	* @param pose 対象のCubismPose
	*/
	static delete(pose) {
		if (pose != null) pose = null;
	}
	/**
	* モデルのパラメータの更新
	* @param model 対象のモデル
	* @param deltaTimeSeconds デルタ時間[秒]
	*/
	updateParameters(model, deltaTimeSeconds) {
		if (model != this._lastModel) this.reset(model);
		this._lastModel = model;
		if (deltaTimeSeconds < 0) deltaTimeSeconds = 0;
		let beginIndex = 0;
		for (let i = 0; i < this._partGroupCounts.length; i++) {
			const partGroupCount = this._partGroupCounts[i];
			this.doFade(model, deltaTimeSeconds, beginIndex, partGroupCount);
			beginIndex += partGroupCount;
		}
		this.copyPartOpacities(model);
	}
	/**
	* 表示を初期化
	* @param model 対象のモデル
	* @note 不透明度の初期値が0でないパラメータは、不透明度を１に設定する
	*/
	reset(model) {
		let beginIndex = 0;
		for (let i = 0; i < this._partGroupCounts.length; ++i) {
			const groupCount = this._partGroupCounts[i];
			for (let j = beginIndex; j < beginIndex + groupCount; ++j) {
				this._partGroups[j].initialize(model);
				const partsIndex = this._partGroups[j].partIndex;
				const paramIndex = this._partGroups[j].parameterIndex;
				if (partsIndex < 0) continue;
				model.setPartOpacityByIndex(partsIndex, j == beginIndex ? 1 : 0);
				model.setParameterValueByIndex(paramIndex, j == beginIndex ? 1 : 0);
				for (let k = 0; k < this._partGroups[j].link.length; ++k) this._partGroups[j].link[k].initialize(model);
			}
			beginIndex += groupCount;
		}
	}
	/**
	* パーツの不透明度をコピー
	*
	* @param model 対象のモデル
	*/
	copyPartOpacities(model) {
		for (let groupIndex = 0; groupIndex < this._partGroups.length; ++groupIndex) {
			const partData = this._partGroups[groupIndex];
			if (partData.link.length == 0) continue;
			const partIndex = this._partGroups[groupIndex].partIndex;
			const opacity = model.getPartOpacityByIndex(partIndex);
			for (let linkIndex = 0; linkIndex < partData.link.length; ++linkIndex) {
				const linkPartIndex = partData.link[linkIndex].partIndex;
				if (linkPartIndex < 0) continue;
				model.setPartOpacityByIndex(linkPartIndex, opacity);
			}
		}
	}
	/**
	* パーツのフェード操作を行う。
	* @param model 対象のモデル
	* @param deltaTimeSeconds デルタ時間[秒]
	* @param beginIndex フェード操作を行うパーツグループの先頭インデックス
	* @param partGroupCount フェード操作を行うパーツグループの個数
	*/
	doFade(model, deltaTimeSeconds, beginIndex, partGroupCount) {
		let visiblePartIndex = -1;
		let newOpacity = 1;
		const phi = .5;
		const backOpacityThreshold = .15;
		for (let i = beginIndex; i < beginIndex + partGroupCount; ++i) {
			const partIndex = this._partGroups[i].partIndex;
			const paramIndex = this._partGroups[i].parameterIndex;
			if (model.getParameterValueByIndex(paramIndex) > Epsilon$1) {
				if (visiblePartIndex >= 0) break;
				visiblePartIndex = i;
				if (this._fadeTimeSeconds == 0) {
					newOpacity = 1;
					continue;
				}
				newOpacity = model.getPartOpacityByIndex(partIndex);
				newOpacity += deltaTimeSeconds / this._fadeTimeSeconds;
				if (newOpacity > 1) newOpacity = 1;
			}
		}
		if (visiblePartIndex < 0) {
			visiblePartIndex = 0;
			newOpacity = 1;
		}
		for (let i = beginIndex; i < beginIndex + partGroupCount; ++i) {
			const partsIndex = this._partGroups[i].partIndex;
			if (visiblePartIndex == i) model.setPartOpacityByIndex(partsIndex, newOpacity);
			else {
				let opacity = model.getPartOpacityByIndex(partsIndex);
				let a1;
				if (newOpacity < phi) a1 = newOpacity * -.5 / phi + 1;
				else a1 = (1 - newOpacity) * phi / .5;
				if ((1 - a1) * (1 - newOpacity) > backOpacityThreshold) a1 = 1 - backOpacityThreshold / (1 - newOpacity);
				if (opacity > a1) opacity = a1;
				model.setPartOpacityByIndex(partsIndex, opacity);
			}
		}
	}
	/**
	* コンストラクタ
	*/
	constructor() {
		this._fadeTimeSeconds = DefaultFadeInSeconds;
		this._lastModel = null;
		this._partGroups = new Array();
		this._partGroupCounts = new Array();
	}
};
/**
* パーツにまつわるデータを管理
*/
var PartData = class PartData {
	/**
	* コンストラクタ
	*/
	constructor(v) {
		this.parameterIndex = 0;
		this.partIndex = 0;
		this.link = new Array();
		if (v != void 0) {
			this.partId = v.partId;
			this.link.length = v.link.length;
			for (let i = 0; i < v.link.length; i++) this.link[i] = v.link[i].clone();
		}
	}
	/**
	* =演算子のオーバーロード
	*/
	assignment(v) {
		this.partId = v.partId;
		let dstIndex = this.link.length;
		this.link.length += v.link.length;
		for (const partData of v.link) this.link[dstIndex++] = partData.clone();
		return this;
	}
	/**
	* 初期化
	* @param model 初期化に使用するモデル
	*/
	initialize(model) {
		this.parameterIndex = model.getParameterIndex(this.partId);
		this.partIndex = model.getPartIndex(this.partId);
		model.setParameterValueByIndex(this.parameterIndex, 1);
	}
	/**
	* オブジェクトのコピーを生成する
	*/
	clone() {
		const clonePartData = new PartData();
		clonePartData.partId = this.partId;
		clonePartData.parameterIndex = this.parameterIndex;
		clonePartData.partIndex = this.partIndex;
		clonePartData.link = new Array();
		clonePartData.link.length = this.link.length;
		for (let i = 0; i < this.link.length; i++) clonePartData.link[i] = this.link[i].clone();
		return clonePartData;
	}
};
var Live2DCubismFramework$22;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismPose = CubismPose;
	_Live2DCubismFramework.PartData = PartData;
})(Live2DCubismFramework$22 || (Live2DCubismFramework$22 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/math/cubismmodelmatrix.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
/**
* モデル座標設定用の4x4行列
*
* モデル座標設定用の4x4行列クラス
*/
var CubismModelMatrix = class extends CubismMatrix44 {
	/**
	* コンストラクタ
	*
	* @param w 横幅
	* @param h 縦幅
	*/
	constructor(w, h) {
		super();
		this._width = w !== void 0 ? w : 0;
		this._height = h !== void 0 ? h : 0;
		this.setHeight(2);
	}
	/**
	* 横幅を設定
	*
	* @param w 横幅
	*/
	setWidth(w) {
		const scaleX = w / this._width;
		const scaleY = scaleX;
		this.scale(scaleX, scaleY);
	}
	/**
	* 縦幅を設定
	* @param h 縦幅
	*/
	setHeight(h) {
		const scaleX = h / this._height;
		const scaleY = scaleX;
		this.scale(scaleX, scaleY);
	}
	/**
	* 位置を設定
	*
	* @param x X軸の位置
	* @param y Y軸の位置
	*/
	setPosition(x, y) {
		this.translate(x, y);
	}
	/**
	* 中心位置を設定
	*
	* @param x X軸の中心位置
	* @param y Y軸の中心位置
	*
	* @note widthかheightを設定したあとでないと、拡大率が正しく取得できないためずれる。
	*/
	setCenterPosition(x, y) {
		this.centerX(x);
		this.centerY(y);
	}
	/**
	* 上辺の位置を設定する
	*
	* @param y 上辺のY軸位置
	*/
	top(y) {
		this.setY(y);
	}
	/**
	* 下辺の位置を設定する
	*
	* @param y 下辺のY軸位置
	*/
	bottom(y) {
		const h = this._height * this.getScaleY();
		this.translateY(y - h);
	}
	/**
	* 左辺の位置を設定
	*
	* @param x 左辺のX軸位置
	*/
	left(x) {
		this.setX(x);
	}
	/**
	* 右辺の位置を設定
	*
	* @param x 右辺のX軸位置
	*/
	right(x) {
		const w = this._width * this.getScaleX();
		this.translateX(x - w);
	}
	/**
	* X軸の中心位置を設定
	*
	* @param x X軸の中心位置
	*/
	centerX(x) {
		const w = this._width * this.getScaleX();
		this.translateX(x - w / 2);
	}
	/**
	* X軸の位置を設定
	*
	* @param x X軸の位置
	*/
	setX(x) {
		this.translateX(x);
	}
	/**
	* Y軸の中心位置を設定
	*
	* @param y Y軸の中心位置
	*/
	centerY(y) {
		const h = this._height * this.getScaleY();
		this.translateY(y - h / 2);
	}
	/**
	* Y軸の位置を設定する
	*
	* @param y Y軸の位置
	*/
	setY(y) {
		this.translateY(y);
	}
	/**
	* レイアウト情報から位置を設定
	*
	* @param layout レイアウト情報
	*/
	setupFromLayout(layout) {
		const keyWidth = "width";
		const keyHeight = "height";
		const keyX = "x";
		const keyY = "y";
		const keyCenterX = "center_x";
		const keyCenterY = "center_y";
		const keyTop = "top";
		const keyBottom = "bottom";
		const keyLeft = "left";
		const keyRight = "right";
		for (const item of layout) {
			const key = item[0];
			const value = item[1];
			if (key == keyWidth) this.setWidth(value);
			else if (key == keyHeight) this.setHeight(value);
		}
		for (const item of layout) {
			const key = item[0];
			const value = item[1];
			if (key == keyX) this.setX(value);
			else if (key == keyY) this.setY(value);
			else if (key == keyCenterX) this.centerX(value);
			else if (key == keyCenterY) this.centerY(value);
			else if (key == keyTop) this.top(value);
			else if (key == keyBottom) this.bottom(value);
			else if (key == keyLeft) this.left(value);
			else if (key == keyRight) this.right(value);
		}
	}
};
var Live2DCubismFramework$21;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismModelMatrix = CubismModelMatrix;
})(Live2DCubismFramework$21 || (Live2DCubismFramework$21 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/math/cubismtargetpoint.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
var FrameRate = 30;
var Epsilon = .01;
/**
* 顔の向きの制御機能
*
* 顔の向きの制御機能を提供するクラス。
*/
var CubismTargetPoint = class {
	/**
	* コンストラクタ
	*/
	constructor() {
		this._faceTargetX = 0;
		this._faceTargetY = 0;
		this._faceX = 0;
		this._faceY = 0;
		this._faceVX = 0;
		this._faceVY = 0;
		this._lastTimeSeconds = 0;
		this._userTimeSeconds = 0;
	}
	/**
	* 更新処理
	*/
	update(deltaTimeSeconds) {
		this._userTimeSeconds += deltaTimeSeconds;
		const maxV = 4 / FrameRate;
		if (this._lastTimeSeconds == 0) {
			this._lastTimeSeconds = this._userTimeSeconds;
			return;
		}
		const deltaTimeWeight = (this._userTimeSeconds - this._lastTimeSeconds) * FrameRate;
		this._lastTimeSeconds = this._userTimeSeconds;
		const frameToMaxSpeed = .15 * FrameRate;
		const maxA = deltaTimeWeight * maxV / frameToMaxSpeed;
		const dx = this._faceTargetX - this._faceX;
		const dy = this._faceTargetY - this._faceY;
		if (CubismMath.abs(dx) <= Epsilon && CubismMath.abs(dy) <= Epsilon) return;
		const d = CubismMath.sqrt(dx * dx + dy * dy);
		const vx = maxV * dx / d;
		const vy = maxV * dy / d;
		let ax = vx - this._faceVX;
		let ay = vy - this._faceVY;
		const a = CubismMath.sqrt(ax * ax + ay * ay);
		if (a < -maxA || a > maxA) {
			ax *= maxA / a;
			ay *= maxA / a;
		}
		this._faceVX += ax;
		this._faceVY += ay;
		{
			const maxV = .5 * (CubismMath.sqrt(maxA * maxA + 16 * maxA * d - 8 * maxA * d) - maxA);
			const curV = CubismMath.sqrt(this._faceVX * this._faceVX + this._faceVY * this._faceVY);
			if (curV > maxV) {
				this._faceVX *= maxV / curV;
				this._faceVY *= maxV / curV;
			}
		}
		this._faceX += this._faceVX;
		this._faceY += this._faceVY;
	}
	/**
	* X軸の顔の向きの値を取得
	*
	* @return X軸の顔の向きの値（-1.0 ~ 1.0）
	*/
	getX() {
		return this._faceX;
	}
	/**
	* Y軸の顔の向きの値を取得
	*
	* @return Y軸の顔の向きの値（-1.0 ~ 1.0）
	*/
	getY() {
		return this._faceY;
	}
	/**
	* 顔の向きの目標値を設定
	*
	* @param x X軸の顔の向きの値（-1.0 ~ 1.0）
	* @param y Y軸の顔の向きの値（-1.0 ~ 1.0）
	*/
	set(x, y) {
		this._faceTargetX = x;
		this._faceTargetY = y;
	}
};
var Live2DCubismFramework$20;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismTargetPoint = CubismTargetPoint;
})(Live2DCubismFramework$20 || (Live2DCubismFramework$20 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/motion/acubismmotion.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
/**
* モーションの抽象基底クラス
*
* モーションの抽象基底クラス。MotionQueueManagerによってモーションの再生を管理する。
*/
var ACubismMotion = class {
	/**
	* インスタンスの破棄
	*/
	static delete(motion) {
		motion.release();
		motion = null;
	}
	/**
	* コンストラクタ
	*/
	constructor() {
		this.setBeganMotionHandler = (onBeganMotionHandler) => this._onBeganMotion = onBeganMotionHandler;
		this.getBeganMotionHandler = () => this._onBeganMotion;
		this.setFinishedMotionHandler = (onFinishedMotionHandler) => this._onFinishedMotion = onFinishedMotionHandler;
		this.getFinishedMotionHandler = () => this._onFinishedMotion;
		this._fadeInSeconds = -1;
		this._fadeOutSeconds = -1;
		this._weight = 1;
		this._offsetSeconds = 0;
		this._isLoop = false;
		this._isLoopFadeIn = true;
		this._previousLoopState = this._isLoop;
		this._firedEventValues = new Array();
	}
	/**
	* デストラクタ相当の処理
	*/
	release() {
		this._weight = 0;
	}
	/**
	* モデルのパラメータ
	* @param model 対象のモデル
	* @param motionQueueEntry CubismMotionQueueManagerで管理されているモーション
	* @param userTimeSeconds デルタ時間の積算値[秒]
	*/
	updateParameters(model, motionQueueEntry, userTimeSeconds) {
		if (!motionQueueEntry.isAvailable() || motionQueueEntry.isFinished()) return;
		this.setupMotionQueueEntry(motionQueueEntry, userTimeSeconds);
		const fadeWeight = this.updateFadeWeight(motionQueueEntry, userTimeSeconds);
		this.doUpdateParameters(model, userTimeSeconds, fadeWeight, motionQueueEntry);
		if (motionQueueEntry.getEndTime() > 0 && motionQueueEntry.getEndTime() < userTimeSeconds) motionQueueEntry.setIsFinished(true);
	}
	/**
	* @brief モデルの再生開始処理
	*
	* モーションの再生を開始するためのセットアップを行う。
	*
	* @param[in]   motionQueueEntry    CubismMotionQueueManagerで管理されているモーション
	* @param[in]   userTimeSeconds     デルタ時間の積算値[秒]
	*/
	setupMotionQueueEntry(motionQueueEntry, userTimeSeconds) {
		if (motionQueueEntry == null || motionQueueEntry.isStarted()) return;
		if (!motionQueueEntry.isAvailable()) return;
		motionQueueEntry.setIsStarted(true);
		motionQueueEntry.setStartTime(userTimeSeconds - this._offsetSeconds);
		motionQueueEntry.setFadeInStartTime(userTimeSeconds);
		if (motionQueueEntry.getEndTime() < 0) this.adjustEndTime(motionQueueEntry);
		if (motionQueueEntry._motion._onBeganMotion) motionQueueEntry._motion._onBeganMotion(motionQueueEntry._motion);
	}
	/**
	* @brief モデルのウェイト更新
	*
	* モーションのウェイトを更新する。
	*
	* @param[in]   motionQueueEntry    CubismMotionQueueManagerで管理されているモーション
	* @param[in]   userTimeSeconds     デルタ時間の積算値[秒]
	*/
	updateFadeWeight(motionQueueEntry, userTimeSeconds) {
		if (motionQueueEntry == null) CubismDebug.print(LogLevel.LogLevel_Error, "motionQueueEntry is null.");
		let fadeWeight = this._weight;
		const fadeIn = this._fadeInSeconds == 0 ? 1 : CubismMath.getEasingSine((userTimeSeconds - motionQueueEntry.getFadeInStartTime()) / this._fadeInSeconds);
		const fadeOut = this._fadeOutSeconds == 0 || motionQueueEntry.getEndTime() < 0 ? 1 : CubismMath.getEasingSine((motionQueueEntry.getEndTime() - userTimeSeconds) / this._fadeOutSeconds);
		fadeWeight = fadeWeight * fadeIn * fadeOut;
		motionQueueEntry.setState(userTimeSeconds, fadeWeight);
		CSM_ASSERT(0 <= fadeWeight && fadeWeight <= 1);
		return fadeWeight;
	}
	/**
	* フェードインの時間を設定する
	* @param fadeInSeconds フェードインにかかる時間[秒]
	*/
	setFadeInTime(fadeInSeconds) {
		this._fadeInSeconds = fadeInSeconds;
	}
	/**
	* フェードアウトの時間を設定する
	* @param fadeOutSeconds フェードアウトにかかる時間[秒]
	*/
	setFadeOutTime(fadeOutSeconds) {
		this._fadeOutSeconds = fadeOutSeconds;
	}
	/**
	* フェードアウトにかかる時間の取得
	* @return フェードアウトにかかる時間[秒]
	*/
	getFadeOutTime() {
		return this._fadeOutSeconds;
	}
	/**
	* フェードインにかかる時間の取得
	* @return フェードインにかかる時間[秒]
	*/
	getFadeInTime() {
		return this._fadeInSeconds;
	}
	/**
	* モーション適用の重みの設定
	* @param weight 重み（0.0 - 1.0）
	*/
	setWeight(weight) {
		this._weight = weight;
	}
	/**
	* モーション適用の重みの取得
	* @return 重み（0.0 - 1.0）
	*/
	getWeight() {
		return this._weight;
	}
	/**
	* モーションの長さの取得
	* @return モーションの長さ[秒]
	*
	* @note ループの時は「-1」。
	*       ループでない場合は、オーバーライドする。
	*       正の値の時は取得される時間で終了する。
	*       「-1」の時は外部から停止命令がない限り終わらない処理となる。
	*/
	getDuration() {
		return -1;
	}
	/**
	* モーションのループ1回分の長さの取得
	* @return モーションのループ一回分の長さ[秒]
	*
	* @note ループしない場合は、getDuration()と同じ値を返す
	*       ループ一回分の長さが定義できない場合(プログラム的に動き続けるサブクラスなど)の場合は「-1」を返す
	*/
	getLoopDuration() {
		return -1;
	}
	/**
	* モーション再生の開始時刻の設定
	* @param offsetSeconds モーション再生の開始時刻[秒]
	*/
	setOffsetTime(offsetSeconds) {
		this._offsetSeconds = offsetSeconds;
	}
	/**
	* ループ情報の設定
	* @param loop ループ情報
	*/
	setLoop(loop) {
		this._isLoop = loop;
	}
	/**
	* ループ情報の取得
	* @return true ループする
	* @return false ループしない
	*/
	getLoop() {
		return this._isLoop;
	}
	/**
	* ループ時のフェードイン情報の設定
	* @param loopFadeIn  ループ時のフェードイン情報
	*/
	setLoopFadeIn(loopFadeIn) {
		this._isLoopFadeIn = loopFadeIn;
	}
	/**
	* ループ時のフェードイン情報の取得
	*
	* @return  true    する
	* @return  false   しない
	*/
	getLoopFadeIn() {
		return this._isLoopFadeIn;
	}
	/**
	* モデルのパラメータ更新
	*
	* イベント発火のチェック。
	* 入力する時間は呼ばれるモーションタイミングを０とした秒数で行う。
	*
	* @param beforeCheckTimeSeconds 前回のイベントチェック時間[秒]
	* @param motionTimeSeconds 今回の再生時間[秒]
	*/
	getFiredEvent(beforeCheckTimeSeconds, motionTimeSeconds) {
		return this._firedEventValues;
	}
	/**
	* 透明度のカーブが存在するかどうかを確認する
	*
	* @return true  -> キーが存在する
	*          false -> キーが存在しない
	*/
	isExistModelOpacity() {
		return false;
	}
	/**
	* 透明度のカーブのインデックスを返す
	*
	* @return success:透明度のカーブのインデックス
	*/
	getModelOpacityIndex() {
		return -1;
	}
	/**
	* 透明度のIdを返す
	*
	* @param index モーションカーブのインデックス
	* @return success:透明度のId
	*/
	getModelOpacityId(index) {
		return null;
	}
	/**
	* 指定時間の透明度の値を返す
	*
	* @return success:モーションの現在時間におけるOpacityの値
	*
	* @note  更新後の値を取るにはUpdateParameters() の後に呼び出す。
	*/
	getModelOpacityValue() {
		return 1;
	}
	/**
	* 終了時刻の調整
	* @param motionQueueEntry CubismMotionQueueManagerで管理されているモーション
	*/
	adjustEndTime(motionQueueEntry) {
		const duration = this.getDuration();
		const endTime = duration <= 0 ? -1 : motionQueueEntry.getStartTime() + duration;
		motionQueueEntry.setEndTime(endTime);
	}
};
var Live2DCubismFramework$19;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.ACubismMotion = ACubismMotion;
})(Live2DCubismFramework$19 || (Live2DCubismFramework$19 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/motion/cubismexpressionmotion.ts
var ExpressionKeyFadeIn = "FadeInTime";
var ExpressionKeyFadeOut = "FadeOutTime";
var ExpressionKeyParameters = "Parameters";
var ExpressionKeyId = "Id";
var ExpressionKeyValue = "Value";
var ExpressionKeyBlend = "Blend";
var BlendValueAdd = "Add";
var BlendValueMultiply = "Multiply";
var BlendValueOverwrite = "Overwrite";
var DefaultFadeTime = 1;
/**
* 表情のモーション
*
* 表情のモーションクラス。
*/
var CubismExpressionMotion = class CubismExpressionMotion extends ACubismMotion {
	static {
		this.DefaultAdditiveValue = 0;
	}
	static {
		this.DefaultMultiplyValue = 1;
	}
	/**
	* インスタンスを作成する。
	* @param buffer expファイルが読み込まれているバッファ
	* @param size バッファのサイズ
	* @return 作成されたインスタンス
	*/
	static create(buffer, size) {
		const expression = new CubismExpressionMotion();
		expression.parse(buffer, size);
		return expression;
	}
	/**
	* モデルのパラメータの更新の実行
	* @param model 対象のモデル
	* @param userTimeSeconds デルタ時間の積算値[秒]
	* @param weight モーションの重み
	* @param motionQueueEntry CubismMotionQueueManagerで管理されているモーション
	*/
	doUpdateParameters(model, userTimeSeconds, weight, motionQueueEntry) {
		for (let i = 0; i < this._parameters.length; ++i) {
			const parameter = this._parameters[i];
			switch (parameter.blendType) {
				case 0:
					model.addParameterValueById(parameter.parameterId, parameter.value, weight);
					break;
				case 1:
					model.multiplyParameterValueById(parameter.parameterId, parameter.value, weight);
					break;
				case 2: model.setParameterValueById(parameter.parameterId, parameter.value, weight);
			}
		}
	}
	/**
	* @brief 表情によるモデルのパラメータの計算
	*
	* モデルの表情に関するパラメータを計算する。
	*
	* @param[in]   model                        対象のモデル
	* @param[in]   userTimeSeconds              デルタ時間の積算値[秒]
	* @param[in]   motionQueueEntry             CubismMotionQueueManagerで管理されているモーション
	* @param[in]   expressionParameterValues    モデルに適用する各パラメータの値
	* @param[in]   expressionIndex              表情のインデックス
	* @param[in]   fadeWeight                   表情のウェイト
	*/
	calculateExpressionParameters(model, userTimeSeconds, motionQueueEntry, expressionParameterValues, expressionIndex, fadeWeight) {
		if (motionQueueEntry == null || expressionParameterValues == null) return;
		if (!motionQueueEntry.isAvailable()) return;
		for (let i = 0; i < expressionParameterValues.length; ++i) {
			const expressionParameterValue = expressionParameterValues[i];
			if (expressionParameterValue.parameterId == null) continue;
			const currentParameterValue = expressionParameterValue.overwriteValue = model.getParameterValueById(expressionParameterValue.parameterId);
			const expressionParameters = this.getExpressionParameters();
			let parameterIndex = -1;
			for (let j = 0; j < expressionParameters.length; ++j) {
				if (expressionParameterValue.parameterId != expressionParameters[j].parameterId) continue;
				parameterIndex = j;
				break;
			}
			if (parameterIndex < 0) {
				if (expressionIndex == 0) {
					expressionParameterValue.additiveValue = CubismExpressionMotion.DefaultAdditiveValue;
					expressionParameterValue.multiplyValue = CubismExpressionMotion.DefaultMultiplyValue;
					expressionParameterValue.overwriteValue = currentParameterValue;
				} else {
					expressionParameterValue.additiveValue = this.calculateValue(expressionParameterValue.additiveValue, CubismExpressionMotion.DefaultAdditiveValue, fadeWeight);
					expressionParameterValue.multiplyValue = this.calculateValue(expressionParameterValue.multiplyValue, CubismExpressionMotion.DefaultMultiplyValue, fadeWeight);
					expressionParameterValue.overwriteValue = this.calculateValue(expressionParameterValue.overwriteValue, currentParameterValue, fadeWeight);
				}
				continue;
			}
			const value = expressionParameters[parameterIndex].value;
			let newAdditiveValue, newMultiplyValue, newOverwriteValue;
			switch (expressionParameters[parameterIndex].blendType) {
				case 0:
					newAdditiveValue = value;
					newMultiplyValue = CubismExpressionMotion.DefaultMultiplyValue;
					newOverwriteValue = currentParameterValue;
					break;
				case 1:
					newAdditiveValue = CubismExpressionMotion.DefaultAdditiveValue;
					newMultiplyValue = value;
					newOverwriteValue = currentParameterValue;
					break;
				case 2:
					newAdditiveValue = CubismExpressionMotion.DefaultAdditiveValue;
					newMultiplyValue = CubismExpressionMotion.DefaultMultiplyValue;
					newOverwriteValue = value;
					break;
				default: return;
			}
			if (expressionIndex == 0) {
				expressionParameterValue.additiveValue = newAdditiveValue;
				expressionParameterValue.multiplyValue = newMultiplyValue;
				expressionParameterValue.overwriteValue = newOverwriteValue;
			} else {
				expressionParameterValue.additiveValue = expressionParameterValue.additiveValue * (1 - fadeWeight) + newAdditiveValue * fadeWeight;
				expressionParameterValue.multiplyValue = expressionParameterValue.multiplyValue * (1 - fadeWeight) + newMultiplyValue * fadeWeight;
				expressionParameterValue.overwriteValue = expressionParameterValue.overwriteValue * (1 - fadeWeight) + newOverwriteValue * fadeWeight;
			}
		}
	}
	/**
	* @brief 表情が参照しているパラメータを取得
	*
	* 表情が参照しているパラメータを取得する
	*
	* @return 表情パラメータ
	*/
	getExpressionParameters() {
		return this._parameters;
	}
	parse(buffer, size) {
		const json = CubismJson.create(buffer, size);
		if (!json) return;
		const root = json.getRoot();
		this.setFadeInTime(root.getValueByString(ExpressionKeyFadeIn).toFloat(DefaultFadeTime));
		this.setFadeOutTime(root.getValueByString(ExpressionKeyFadeOut).toFloat(DefaultFadeTime));
		const parameterCount = root.getValueByString(ExpressionKeyParameters).getSize();
		let dstIndex = this._parameters.length;
		this._parameters.length += parameterCount;
		for (let i = 0; i < parameterCount; ++i) {
			const param = root.getValueByString(ExpressionKeyParameters).getValueByIndex(i);
			const parameterId = CubismFramework.getIdManager().getId(param.getValueByString(ExpressionKeyId).getRawString());
			const value = param.getValueByString(ExpressionKeyValue).toFloat();
			let blendType;
			if (param.getValueByString(ExpressionKeyBlend).isNull() || param.getValueByString(ExpressionKeyBlend).getString() == BlendValueAdd) blendType = 0;
			else if (param.getValueByString(ExpressionKeyBlend).getString() == BlendValueMultiply) blendType = 1;
			else if (param.getValueByString(ExpressionKeyBlend).getString() == BlendValueOverwrite) blendType = 2;
			else blendType = 0;
			const item = new ExpressionParameter();
			item.parameterId = parameterId;
			item.blendType = blendType;
			item.value = value;
			this._parameters[dstIndex++] = item;
		}
		CubismJson.delete(json);
	}
	/**
	* @brief ブレンド計算
	*
	* 入力された値でブレンド計算をする。
	*
	* @param source 現在の値
	* @param destination 適用する値
	* @param weight ウェイト
	* @return 計算結果
	*/
	calculateValue(source, destination, fadeWeight) {
		return source * (1 - fadeWeight) + destination * fadeWeight;
	}
	/**
	* コンストラクタ
	*/
	constructor() {
		super();
		this._parameters = new Array();
	}
};
/**
* 表情パラメータ値の計算方式
*/
var ExpressionBlendType = /* @__PURE__ */ function(ExpressionBlendType) {
	ExpressionBlendType[ExpressionBlendType["Additive"] = 0] = "Additive";
	ExpressionBlendType[ExpressionBlendType["Multiply"] = 1] = "Multiply";
	ExpressionBlendType[ExpressionBlendType["Overwrite"] = 2] = "Overwrite";
	return ExpressionBlendType;
}({});
/**
* 表情のパラメータ情報
*/
var ExpressionParameter = class {};
var Live2DCubismFramework$18;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismExpressionMotion = CubismExpressionMotion;
	_Live2DCubismFramework.ExpressionBlendType = ExpressionBlendType;
	_Live2DCubismFramework.ExpressionParameter = ExpressionParameter;
})(Live2DCubismFramework$18 || (Live2DCubismFramework$18 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/motion/cubismmotionqueueentry.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
/**
* CubismMotionQueueManagerで再生している各モーションの管理クラス。
*/
var CubismMotionQueueEntry = class {
	/**
	* コンストラクタ
	*/
	constructor() {
		this._autoDelete = false;
		this._motion = null;
		this._available = true;
		this._finished = false;
		this._started = false;
		this._startTimeSeconds = -1;
		this._fadeInStartTimeSeconds = 0;
		this._endTimeSeconds = -1;
		this._stateTimeSeconds = 0;
		this._stateWeight = 0;
		this._lastEventCheckSeconds = 0;
		this._motionQueueEntryHandle = this;
		this._fadeOutSeconds = 0;
		this._isTriggeredFadeOut = false;
	}
	/**
	* デストラクタ相当の処理
	*/
	release() {
		if (this._autoDelete && this._motion) ACubismMotion.delete(this._motion);
	}
	/**
	* フェードアウト時間と開始判定の設定
	* @param fadeOutSeconds フェードアウトにかかる時間[秒]
	*/
	setFadeOut(fadeOutSeconds) {
		this._fadeOutSeconds = fadeOutSeconds;
		this._isTriggeredFadeOut = true;
	}
	/**
	* フェードアウトの開始
	* @param fadeOutSeconds フェードアウトにかかる時間[秒]
	* @param userTimeSeconds デルタ時間の積算値[秒]
	*/
	startFadeOut(fadeOutSeconds, userTimeSeconds) {
		const newEndTimeSeconds = userTimeSeconds + fadeOutSeconds;
		this._isTriggeredFadeOut = true;
		if (this._endTimeSeconds < 0 || newEndTimeSeconds < this._endTimeSeconds) this._endTimeSeconds = newEndTimeSeconds;
	}
	/**
	* モーションの終了の確認
	*
	* @return true モーションが終了した
	* @return false 終了していない
	*/
	isFinished() {
		return this._finished;
	}
	/**
	* モーションの開始の確認
	* @return true モーションが開始した
	* @return false 開始していない
	*/
	isStarted() {
		return this._started;
	}
	/**
	* モーションの開始時刻の取得
	* @return モーションの開始時刻[秒]
	*/
	getStartTime() {
		return this._startTimeSeconds;
	}
	/**
	* フェードインの開始時刻の取得
	* @return フェードインの開始時刻[秒]
	*/
	getFadeInStartTime() {
		return this._fadeInStartTimeSeconds;
	}
	/**
	* フェードインの終了時刻の取得
	* @return フェードインの終了時刻の取得
	*/
	getEndTime() {
		return this._endTimeSeconds;
	}
	/**
	* モーションの開始時刻の設定
	* @param startTime モーションの開始時刻
	*/
	setStartTime(startTime) {
		this._startTimeSeconds = startTime;
	}
	/**
	* フェードインの開始時刻の設定
	* @param startTime フェードインの開始時刻[秒]
	*/
	setFadeInStartTime(startTime) {
		this._fadeInStartTimeSeconds = startTime;
	}
	/**
	* フェードインの終了時刻の設定
	* @param endTime フェードインの終了時刻[秒]
	*/
	setEndTime(endTime) {
		this._endTimeSeconds = endTime;
	}
	/**
	* モーションの終了の設定
	* @param f trueならモーションの終了
	*/
	setIsFinished(f) {
		this._finished = f;
	}
	/**
	* モーション開始の設定
	* @param f trueならモーションの開始
	*/
	setIsStarted(f) {
		this._started = f;
	}
	/**
	* モーションの有効性の確認
	* @return true モーションは有効
	* @return false モーションは無効
	*/
	isAvailable() {
		return this._available;
	}
	/**
	* モーションの有効性の設定
	* @param v trueならモーションは有効
	*/
	setIsAvailable(v) {
		this._available = v;
	}
	/**
	* モーションの状態の設定
	* @param timeSeconds 現在時刻[秒]
	* @param weight モーション尾重み
	*/
	setState(timeSeconds, weight) {
		this._stateTimeSeconds = timeSeconds;
		this._stateWeight = weight;
	}
	/**
	* モーションの現在時刻の取得
	* @return モーションの現在時刻[秒]
	*/
	getStateTime() {
		return this._stateTimeSeconds;
	}
	/**
	* モーションの重みの取得
	* @return モーションの重み
	*/
	getStateWeight() {
		return this._stateWeight;
	}
	/**
	* 最後にイベントの発火をチェックした時間を取得
	*
	* @return 最後にイベントの発火をチェックした時間[秒]
	*/
	getLastCheckEventSeconds() {
		return this._lastEventCheckSeconds;
	}
	/**
	* 最後にイベントをチェックした時間を設定
	* @param checkSeconds 最後にイベントをチェックした時間[秒]
	*/
	setLastCheckEventSeconds(checkSeconds) {
		this._lastEventCheckSeconds = checkSeconds;
	}
	/**
	* フェードアウト開始判定の取得
	* @return フェードアウト開始するかどうか
	*/
	isTriggeredFadeOut() {
		return this._isTriggeredFadeOut;
	}
	/**
	* フェードアウト時間の取得
	* @return フェードアウト時間[秒]
	*/
	getFadeOutSeconds() {
		return this._fadeOutSeconds;
	}
	/**
	* モーションの取得
	*
	* @return モーション
	*/
	getCubismMotion() {
		return this._motion;
	}
};
var Live2DCubismFramework$17;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismMotionQueueEntry = CubismMotionQueueEntry;
})(Live2DCubismFramework$17 || (Live2DCubismFramework$17 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/motion/cubismmotionqueuemanager.ts
/**
* モーション再生の管理
*
* モーション再生の管理用クラス。CubismMotionモーションなどACubismMotionのサブクラスを再生するために使用する。
*
* @note 再生中に別のモーションが StartMotion()された場合は、新しいモーションに滑らかに変化し旧モーションは中断する。
*       表情用モーション、体用モーションなどを分けてモーション化した場合など、
*       複数のモーションを同時に再生させる場合は、複数のCubismMotionQueueManagerインスタンスを使用する。
*/
var CubismMotionQueueManager = class {
	/**
	* コンストラクタ
	*/
	constructor() {
		this._userTimeSeconds = 0;
		this._eventCallBack = null;
		this._eventCustomData = null;
		this._motions = new Array();
	}
	/**
	* デストラクタ
	*/
	release() {
		for (let i = 0; i < this._motions.length; ++i) if (this._motions[i]) {
			this._motions[i].release();
			this._motions[i] = null;
		}
		this._motions = null;
	}
	/**
	* 指定したモーションの開始
	*
	* 指定したモーションを開始する。同じタイプのモーションが既にある場合は、既存のモーションに終了フラグを立て、フェードアウトを開始させる。
	*
	* @param   motion          開始するモーション
	* @param   autoDelete      再生が終了したモーションのインスタンスを削除するなら true
	* @param   userTimeSeconds Deprecated: デルタ時間の積算値[秒] 関数内で参照していないため使用は非推奨。
	* @return                      開始したモーションの識別番号を返す。個別のモーションが終了したか否かを判定するIsFinished()の引数で使用する。開始できない時は「-1」
	*/
	startMotion(motion, autoDelete, userTimeSeconds) {
		if (motion == null) return -1;
		let motionQueueEntry = null;
		for (let i = 0; i < this._motions.length; ++i) {
			motionQueueEntry = this._motions[i];
			if (motionQueueEntry == null) continue;
			motionQueueEntry.setFadeOut(motionQueueEntry._motion.getFadeOutTime());
		}
		motionQueueEntry = new CubismMotionQueueEntry();
		motionQueueEntry._autoDelete = autoDelete;
		motionQueueEntry._motion = motion;
		this._motions.push(motionQueueEntry);
		return motionQueueEntry._motionQueueEntryHandle;
	}
	/**
	* 全てのモーションの終了の確認
	* @return true 全て終了している
	* @return false 終了していない
	*/
	isFinished() {
		for (let i = 0; i < this._motions.length;) {
			let motionQueueEntry = this._motions[i];
			if (motionQueueEntry == null) {
				this._motions.splice(i, 1);
				continue;
			}
			if (motionQueueEntry._motion == null) {
				motionQueueEntry.release();
				motionQueueEntry = null;
				this._motions.splice(i, 1);
				continue;
			}
			if (!motionQueueEntry.isFinished()) return false;
			else i++;
		}
		return true;
	}
	/**
	* 指定したモーションの終了の確認
	* @param motionQueueEntryNumber モーションの識別番号
	* @return true 全て終了している
	* @return false 終了していない
	*/
	isFinishedByHandle(motionQueueEntryNumber) {
		for (let i = 0; i < this._motions.length; i++) {
			const motionQueueEntry = this._motions[i];
			if (motionQueueEntry == null) continue;
			if (motionQueueEntry._motionQueueEntryHandle == motionQueueEntryNumber && !motionQueueEntry.isFinished()) return false;
		}
		return true;
	}
	/**
	* 全てのモーションを停止する
	*/
	stopAllMotions() {
		for (let i = 0; i < this._motions.length; i++) {
			const motionQueueEntry = this._motions[i];
			if (motionQueueEntry == null) {
				this._motions.splice(i, 1);
				continue;
			}
			motionQueueEntry.release();
			this._motions.splice(i, 1);
		}
	}
	/**
	* @brief CubismMotionQueueEntryの配列の取得
	*
	* CubismMotionQueueEntryの配列を取得する。
	*
	* @return  CubismMotionQueueEntryの配列へのポインタ
	*          NULL   見つからなかった
	*/
	getCubismMotionQueueEntries() {
		return this._motions;
	}
	/**
	* 指定したCubismMotionQueueEntryの取得
	
	* @param   motionQueueEntryNumber  モーションの識別番号
	* @return  指定したCubismMotionQueueEntry
	* @return  null   見つからなかった
	*/
	getCubismMotionQueueEntry(motionQueueEntryNumber) {
		for (let i = 0; i < this._motions.length; i++) {
			const motionQueueEntry = this._motions[i];
			if (motionQueueEntry == null) continue;
			if (motionQueueEntry._motionQueueEntryHandle == motionQueueEntryNumber) return motionQueueEntry;
		}
		return null;
	}
	/**
	* イベントを受け取るCallbackの登録
	*
	* @param callback コールバック関数
	* @param customData コールバックに返されるデータ
	*/
	setEventCallback(callback, customData = null) {
		this._eventCallBack = callback;
		this._eventCustomData = customData;
	}
	/**
	* モーションを更新して、モデルにパラメータ値を反映する。
	*
	* @param   model   対象のモデル
	* @param   userTimeSeconds   デルタ時間の積算値[秒]
	* @return  true    モデルへパラメータ値の反映あり
	* @return  false   モデルへパラメータ値の反映なし(モーションの変化なし)
	*/
	doUpdateMotion(model, userTimeSeconds) {
		let updated = false;
		for (let i = 0; i < this._motions.length;) {
			let motionQueueEntry = this._motions[i];
			if (motionQueueEntry == null) {
				this._motions.splice(i, 1);
				continue;
			}
			const motion = motionQueueEntry._motion;
			if (motion == null) {
				motionQueueEntry.release();
				motionQueueEntry = null;
				this._motions.splice(i, 1);
				continue;
			}
			motion.updateParameters(model, motionQueueEntry, userTimeSeconds);
			updated = true;
			const firedList = motion.getFiredEvent(motionQueueEntry.getLastCheckEventSeconds() - motionQueueEntry.getStartTime(), userTimeSeconds - motionQueueEntry.getStartTime());
			for (let i = 0; i < firedList.length; ++i) this._eventCallBack(this, firedList[i], this._eventCustomData);
			motionQueueEntry.setLastCheckEventSeconds(userTimeSeconds);
			if (motionQueueEntry.isFinished()) {
				motionQueueEntry.release();
				motionQueueEntry = null;
				this._motions.splice(i, 1);
			} else {
				if (motionQueueEntry.isTriggeredFadeOut()) motionQueueEntry.startFadeOut(motionQueueEntry.getFadeOutSeconds(), userTimeSeconds);
				i++;
			}
		}
		return updated;
	}
};
var Live2DCubismFramework$16;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismMotionQueueManager = CubismMotionQueueManager;
	_Live2DCubismFramework.InvalidMotionQueueEntryHandleValue = -1;
})(Live2DCubismFramework$16 || (Live2DCubismFramework$16 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/motion/cubismexpressionmotionmanager.ts
/**
* @brief パラメータに適用する表情の値を持たせる構造体
*/
var ExpressionParameterValue = class {};
/**
* @brief 表情モーションの管理
*
* 表情モーションの管理をおこなうクラス。
*/
var CubismExpressionMotionManager = class extends CubismMotionQueueManager {
	/**
	* コンストラクタ
	*/
	constructor() {
		super();
		this._expressionParameterValues = new Array();
		this._fadeWeights = new Array();
	}
	/**
	* デストラクタ相当の処理
	*/
	release() {
		if (this._expressionParameterValues) {
			csmDelete(this._expressionParameterValues);
			this._expressionParameterValues = null;
		}
		if (this._fadeWeights) {
			csmDelete(this._fadeWeights);
			this._fadeWeights = null;
		}
	}
	/**
	* @brief 再生中のモーションのウェイトを取得する。
	*
	* @param[in]    index    表情のインデックス
	* @return               表情モーションのウェイト
	*/
	getFadeWeight(index) {
		if (index < 0 || this._fadeWeights.length < 1 || index >= this._fadeWeights.length) {
			console.warn("Failed to get the fade weight value. The element at that index does not exist.");
			return -1;
		}
		return this._fadeWeights[index];
	}
	/**
	* @brief モーションのウェイトの設定。
	*
	* @param[in]    index    表情のインデックス
	* @param[in]    index    表情モーションのウェイト
	*/
	setFadeWeight(index, expressionFadeWeight) {
		if (index < 0 || this._fadeWeights.length < 1 || this._fadeWeights.length <= index) {
			console.warn("Failed to set the fade weight value. The element at that index does not exist.");
			return;
		}
		this._fadeWeights[index] = expressionFadeWeight;
	}
	/**
	* @brief モーションの更新
	*
	* モーションを更新して、モデルにパラメータ値を反映する。
	*
	* @param[in]   model   対象のモデル
	* @param[in]   deltaTimeSeconds    デルタ時間[秒]
	* @return  true    更新されている
	*          false   更新されていない
	*/
	updateMotion(model, deltaTimeSeconds) {
		this._userTimeSeconds += deltaTimeSeconds;
		let updated = false;
		const motions = this.getCubismMotionQueueEntries();
		let expressionWeight = 0;
		let expressionIndex = 0;
		if (this._fadeWeights.length !== motions.length) {
			const difference = motions.length - this._fadeWeights.length;
			let dstIndex = this._fadeWeights.length;
			this._fadeWeights.length += difference;
			for (let i = 0; i < difference; i++) this._fadeWeights[dstIndex++] = 0;
		}
		for (let i = 0; i < this._motions.length;) {
			const motionQueueEntry = this._motions[i];
			if (motionQueueEntry == null) {
				motions.splice(i, 1);
				continue;
			}
			const expressionMotion = motionQueueEntry.getCubismMotion();
			if (expressionMotion == null) {
				csmDelete(motionQueueEntry);
				motions.splice(i, 1);
				continue;
			}
			const expressionParameters = expressionMotion.getExpressionParameters();
			if (motionQueueEntry.isAvailable()) for (let i = 0; i < expressionParameters.length; ++i) {
				if (expressionParameters[i].parameterId == null) continue;
				let index = -1;
				for (let j = 0; j < this._expressionParameterValues.length; ++j) {
					if (this._expressionParameterValues[j].parameterId != expressionParameters[i].parameterId) continue;
					index = j;
					break;
				}
				if (index >= 0) continue;
				const item = new ExpressionParameterValue();
				item.parameterId = expressionParameters[i].parameterId;
				item.additiveValue = CubismExpressionMotion.DefaultAdditiveValue;
				item.multiplyValue = CubismExpressionMotion.DefaultMultiplyValue;
				item.overwriteValue = model.getParameterValueById(item.parameterId);
				this._expressionParameterValues.push(item);
			}
			expressionMotion.setupMotionQueueEntry(motionQueueEntry, this._userTimeSeconds);
			this.setFadeWeight(expressionIndex, expressionMotion.updateFadeWeight(motionQueueEntry, this._userTimeSeconds));
			expressionMotion.calculateExpressionParameters(model, this._userTimeSeconds, motionQueueEntry, this._expressionParameterValues, expressionIndex, this.getFadeWeight(expressionIndex));
			expressionWeight += expressionMotion.getFadeInTime() == 0 ? 1 : CubismMath.getEasingSine((this._userTimeSeconds - motionQueueEntry.getFadeInStartTime()) / expressionMotion.getFadeInTime());
			updated = true;
			if (motionQueueEntry.isTriggeredFadeOut()) motionQueueEntry.startFadeOut(motionQueueEntry.getFadeOutSeconds(), this._userTimeSeconds);
			++i;
			++expressionIndex;
		}
		if (motions.length > 1) {
			if (this.getFadeWeight(this._fadeWeights.length - 1) >= 1) for (let i = motions.length - 2; i >= 0; --i) {
				const motionQueueEntry = motions[i];
				csmDelete(motionQueueEntry);
				motions.splice(i, 1);
				this._fadeWeights.splice(i, 1);
			}
		}
		if (expressionWeight > 1) expressionWeight = 1;
		for (let i = 0; i < this._expressionParameterValues.length; ++i) {
			const expressionParameterValue = this._expressionParameterValues[i];
			model.setParameterValueById(expressionParameterValue.parameterId, (expressionParameterValue.overwriteValue + expressionParameterValue.additiveValue) * expressionParameterValue.multiplyValue, expressionWeight);
			expressionParameterValue.additiveValue = CubismExpressionMotion.DefaultAdditiveValue;
			expressionParameterValue.multiplyValue = CubismExpressionMotion.DefaultMultiplyValue;
		}
		return updated;
	}
};
var Live2DCubismFramework$15;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismExpressionMotionManager = CubismExpressionMotionManager;
})(Live2DCubismFramework$15 || (Live2DCubismFramework$15 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/utils/cubismarrayutils.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
/**
* Arrayのサイズを変更する。
* @param curArray
* @param newSize
* @param value
* @param callPlacementNew
*/
function updateSize(curArray, newSize, value = null, callPlacementNew = null) {
	if (curArray.length < newSize) {
		if (callPlacementNew) for (let i = curArray.length; i < newSize; i++) if (typeof value == "function") curArray[i] = JSON.parse(JSON.stringify(new value()));
		else curArray[i] = value;
		else for (let i = curArray.length; i < newSize; i++) curArray[i] = value;
	} else curArray.length = newSize;
}
// examples/packages/live2d_pet/vendor/Framework/src/motion/cubismmotioninternal.ts
/**
* @brief モーションカーブの種類
*
* モーションカーブの種類。
*/
var CubismMotionCurveTarget = /* @__PURE__ */ function(CubismMotionCurveTarget) {
	CubismMotionCurveTarget[CubismMotionCurveTarget["CubismMotionCurveTarget_Model"] = 0] = "CubismMotionCurveTarget_Model";
	CubismMotionCurveTarget[CubismMotionCurveTarget["CubismMotionCurveTarget_Parameter"] = 1] = "CubismMotionCurveTarget_Parameter";
	CubismMotionCurveTarget[CubismMotionCurveTarget["CubismMotionCurveTarget_PartOpacity"] = 2] = "CubismMotionCurveTarget_PartOpacity";
	return CubismMotionCurveTarget;
}({});
/**
* @brief モーションカーブのセグメントの種類
*
* モーションカーブのセグメントの種類。
*/
var CubismMotionSegmentType = /* @__PURE__ */ function(CubismMotionSegmentType) {
	CubismMotionSegmentType[CubismMotionSegmentType["CubismMotionSegmentType_Linear"] = 0] = "CubismMotionSegmentType_Linear";
	CubismMotionSegmentType[CubismMotionSegmentType["CubismMotionSegmentType_Bezier"] = 1] = "CubismMotionSegmentType_Bezier";
	CubismMotionSegmentType[CubismMotionSegmentType["CubismMotionSegmentType_Stepped"] = 2] = "CubismMotionSegmentType_Stepped";
	CubismMotionSegmentType[CubismMotionSegmentType["CubismMotionSegmentType_InverseStepped"] = 3] = "CubismMotionSegmentType_InverseStepped";
	return CubismMotionSegmentType;
}({});
/**
* @brief モーションカーブの制御点
*
* モーションカーブの制御点。
*/
var CubismMotionPoint = class {
	constructor() {
		this.time = 0;
		this.value = 0;
	}
};
/**
* @brief モーションカーブのセグメント
*
* モーションカーブのセグメント。
*/
var CubismMotionSegment = class {
	/**
	* @brief コンストラクタ
	*
	* コンストラクタ。
	*/
	constructor() {
		this.evaluate = null;
		this.basePointIndex = 0;
		this.segmentType = 0;
	}
};
/**
* @brief モーションカーブ
*
* モーションカーブ。
*/
var CubismMotionCurve = class {
	constructor() {
		this.type = 0;
		this.segmentCount = 0;
		this.baseSegmentIndex = 0;
		this.fadeInTime = 0;
		this.fadeOutTime = 0;
	}
};
/**
* イベント。
*/
var CubismMotionEvent = class {
	constructor() {
		this.fireTime = 0;
	}
};
/**
* @brief モーションデータ
*
* モーションデータ。
*/
var CubismMotionData = class {
	constructor() {
		this.duration = 0;
		this.loop = false;
		this.curveCount = 0;
		this.eventCount = 0;
		this.fps = 0;
		this.curves = new Array();
		this.segments = new Array();
		this.points = new Array();
		this.events = new Array();
	}
};
var Live2DCubismFramework$14;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismMotionCurve = CubismMotionCurve;
	_Live2DCubismFramework.CubismMotionCurveTarget = CubismMotionCurveTarget;
	_Live2DCubismFramework.CubismMotionData = CubismMotionData;
	_Live2DCubismFramework.CubismMotionEvent = CubismMotionEvent;
	_Live2DCubismFramework.CubismMotionPoint = CubismMotionPoint;
	_Live2DCubismFramework.CubismMotionSegment = CubismMotionSegment;
	_Live2DCubismFramework.CubismMotionSegmentType = CubismMotionSegmentType;
})(Live2DCubismFramework$14 || (Live2DCubismFramework$14 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/motion/cubismmotionjson.ts
var Meta$2 = "Meta";
var Duration = "Duration";
var Loop = "Loop";
var AreBeziersRestricted = "AreBeziersRestricted";
var CurveCount = "CurveCount";
var Fps$1 = "Fps";
var TotalSegmentCount = "TotalSegmentCount";
var TotalPointCount = "TotalPointCount";
var Curves = "Curves";
var Target$1 = "Target";
var Id$2 = "Id";
var FadeInTime = "FadeInTime";
var FadeOutTime = "FadeOutTime";
var Segments = "Segments";
var UserData$1 = "UserData";
var UserDataCount$1 = "UserDataCount";
var TotalUserDataSize$1 = "TotalUserDataSize";
var Time = "Time";
var Value$1 = "Value";
/**
* motion3.jsonのコンテナ。
*/
var CubismMotionJson = class {
	/**
	* コンストラクタ
	* @param buffer motion3.jsonが読み込まれているバッファ
	* @param size バッファのサイズ
	*/
	constructor(buffer, size) {
		this._json = CubismJson.create(buffer, size);
	}
	/**
	* デストラクタ相当の処理
	*/
	release() {
		CubismJson.delete(this._json);
	}
	/**
	* モーションの長さを取得する
	* @return モーションの長さ[秒]
	*/
	getMotionDuration() {
		return this._json.getRoot().getValueByString(Meta$2).getValueByString(Duration).toFloat();
	}
	/**
	* モーションのループ情報の取得
	* @return true ループする
	* @return false ループしない
	*/
	isMotionLoop() {
		return this._json.getRoot().getValueByString(Meta$2).getValueByString(Loop).toBoolean();
	}
	/**
	*  motion3.jsonファイルの整合性チェック
	*
	* @return 正常なファイルの場合はtrueを返す。
	*/
	hasConsistency() {
		let result = true;
		if (!this._json || !this._json.getRoot()) return false;
		const actualCurveListSize = this._json.getRoot().getValueByString(Curves).getVector().length;
		let actualTotalSegmentCount = 0;
		let actualTotalPointCount = 0;
		for (let curvePosition = 0; curvePosition < actualCurveListSize; ++curvePosition) for (let segmentPosition = 0; segmentPosition < this.getMotionCurveSegmentCount(curvePosition);) {
			if (segmentPosition == 0) {
				actualTotalPointCount += 1;
				segmentPosition += 2;
			}
			switch (this.getMotionCurveSegment(curvePosition, segmentPosition)) {
				case CubismMotionSegmentType.CubismMotionSegmentType_Linear:
					actualTotalPointCount += 1;
					segmentPosition += 3;
					break;
				case CubismMotionSegmentType.CubismMotionSegmentType_Bezier:
					actualTotalPointCount += 3;
					segmentPosition += 7;
					break;
				case CubismMotionSegmentType.CubismMotionSegmentType_Stepped:
					actualTotalPointCount += 1;
					segmentPosition += 3;
					break;
				case CubismMotionSegmentType.CubismMotionSegmentType_InverseStepped:
					actualTotalPointCount += 1;
					segmentPosition += 3;
					break;
				default: CSM_ASSERT(0);
			}
			++actualTotalSegmentCount;
		}
		if (actualCurveListSize != this.getMotionCurveCount()) {
			CubismLogWarning("The number of curves does not match the metadata.");
			result = false;
		}
		if (actualTotalSegmentCount != this.getMotionTotalSegmentCount()) {
			CubismLogWarning("The number of segment does not match the metadata.");
			result = false;
		}
		if (actualTotalPointCount != this.getMotionTotalPointCount()) {
			CubismLogWarning("The number of point does not match the metadata.");
			result = false;
		}
		return result;
	}
	getEvaluationOptionFlag(flagType) {
		if (0 == flagType) return this._json.getRoot().getValueByString(Meta$2).getValueByString(AreBeziersRestricted).toBoolean();
		return false;
	}
	/**
	* モーションカーブの個数の取得
	* @return モーションカーブの個数
	*/
	getMotionCurveCount() {
		return this._json.getRoot().getValueByString(Meta$2).getValueByString(CurveCount).toInt();
	}
	/**
	* モーションのフレームレートの取得
	* @return フレームレート[FPS]
	*/
	getMotionFps() {
		return this._json.getRoot().getValueByString(Meta$2).getValueByString(Fps$1).toFloat();
	}
	/**
	* モーションのセグメントの総合計の取得
	* @return モーションのセグメントの取得
	*/
	getMotionTotalSegmentCount() {
		return this._json.getRoot().getValueByString(Meta$2).getValueByString(TotalSegmentCount).toInt();
	}
	/**
	* モーションのカーブの制御店の総合計の取得
	* @return モーションのカーブの制御点の総合計
	*/
	getMotionTotalPointCount() {
		return this._json.getRoot().getValueByString(Meta$2).getValueByString(TotalPointCount).toInt();
	}
	/**
	* モーションのフェードイン時間の存在
	* @return true 存在する
	* @return false 存在しない
	*/
	isExistMotionFadeInTime() {
		return !this._json.getRoot().getValueByString(Meta$2).getValueByString(FadeInTime).isNull();
	}
	/**
	* モーションのフェードアウト時間の存在
	* @return true 存在する
	* @return false 存在しない
	*/
	isExistMotionFadeOutTime() {
		return !this._json.getRoot().getValueByString(Meta$2).getValueByString(FadeOutTime).isNull();
	}
	/**
	* モーションのフェードイン時間の取得
	* @return フェードイン時間[秒]
	*/
	getMotionFadeInTime() {
		return this._json.getRoot().getValueByString(Meta$2).getValueByString(FadeInTime).toFloat();
	}
	/**
	* モーションのフェードアウト時間の取得
	* @return フェードアウト時間[秒]
	*/
	getMotionFadeOutTime() {
		return this._json.getRoot().getValueByString(Meta$2).getValueByString(FadeOutTime).toFloat();
	}
	/**
	* モーションのカーブの種類の取得
	* @param curveIndex カーブのインデックス
	* @return カーブの種類
	*/
	getMotionCurveTarget(curveIndex) {
		return this._json.getRoot().getValueByString(Curves).getValueByIndex(curveIndex).getValueByString(Target$1).getRawString();
	}
	/**
	* モーションのカーブのIDの取得
	* @param curveIndex カーブのインデックス
	* @return カーブのID
	*/
	getMotionCurveId(curveIndex) {
		return CubismFramework.getIdManager().getId(this._json.getRoot().getValueByString(Curves).getValueByIndex(curveIndex).getValueByString(Id$2).getRawString());
	}
	/**
	* モーションのカーブのフェードイン時間の存在
	* @param curveIndex カーブのインデックス
	* @return true 存在する
	* @return false 存在しない
	*/
	isExistMotionCurveFadeInTime(curveIndex) {
		return !this._json.getRoot().getValueByString(Curves).getValueByIndex(curveIndex).getValueByString(FadeInTime).isNull();
	}
	/**
	* モーションのカーブのフェードアウト時間の存在
	* @param curveIndex カーブのインデックス
	* @return true 存在する
	* @return false 存在しない
	*/
	isExistMotionCurveFadeOutTime(curveIndex) {
		return !this._json.getRoot().getValueByString(Curves).getValueByIndex(curveIndex).getValueByString(FadeOutTime).isNull();
	}
	/**
	* モーションのカーブのフェードイン時間の取得
	* @param curveIndex カーブのインデックス
	* @return フェードイン時間[秒]
	*/
	getMotionCurveFadeInTime(curveIndex) {
		return this._json.getRoot().getValueByString(Curves).getValueByIndex(curveIndex).getValueByString(FadeInTime).toFloat();
	}
	/**
	* モーションのカーブのフェードアウト時間の取得
	* @param curveIndex カーブのインデックス
	* @return フェードアウト時間[秒]
	*/
	getMotionCurveFadeOutTime(curveIndex) {
		return this._json.getRoot().getValueByString(Curves).getValueByIndex(curveIndex).getValueByString(FadeOutTime).toFloat();
	}
	/**
	* モーションのカーブのセグメントの個数を取得する
	* @param curveIndex カーブのインデックス
	* @return モーションのカーブのセグメントの個数
	*/
	getMotionCurveSegmentCount(curveIndex) {
		return this._json.getRoot().getValueByString(Curves).getValueByIndex(curveIndex).getValueByString(Segments).getVector().length;
	}
	/**
	* モーションのカーブのセグメントの値の取得
	* @param curveIndex カーブのインデックス
	* @param segmentIndex セグメントのインデックス
	* @return セグメントの値
	*/
	getMotionCurveSegment(curveIndex, segmentIndex) {
		return this._json.getRoot().getValueByString(Curves).getValueByIndex(curveIndex).getValueByString(Segments).getValueByIndex(segmentIndex).toFloat();
	}
	/**
	* イベントの個数の取得
	* @return イベントの個数
	*/
	getEventCount() {
		return this._json.getRoot().getValueByString(Meta$2).getValueByString(UserDataCount$1).toInt();
	}
	/**
	*  イベントの総文字数の取得
	* @return イベントの総文字数
	*/
	getTotalEventValueSize() {
		return this._json.getRoot().getValueByString(Meta$2).getValueByString(TotalUserDataSize$1).toInt();
	}
	/**
	* イベントの時間の取得
	* @param userDataIndex イベントのインデックス
	* @return イベントの時間[秒]
	*/
	getEventTime(userDataIndex) {
		return this._json.getRoot().getValueByString(UserData$1).getValueByIndex(userDataIndex).getValueByString(Time).toFloat();
	}
	/**
	* イベントの取得
	* @param userDataIndex イベントのインデックス
	* @return イベントの文字列
	*/
	getEventValue(userDataIndex) {
		return this._json.getRoot().getValueByString(UserData$1).getValueByIndex(userDataIndex).getValueByString(Value$1).getRawString();
	}
};
/**
* @brief ベジェカーブの解釈方法のフラグタイプ
*/
var EvaluationOptionFlag = /* @__PURE__ */ function(EvaluationOptionFlag) {
	EvaluationOptionFlag[EvaluationOptionFlag["EvaluationOptionFlag_AreBeziersRistricted"] = 0] = "EvaluationOptionFlag_AreBeziersRistricted";
	return EvaluationOptionFlag;
}({});
var Live2DCubismFramework$13;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismMotionJson = CubismMotionJson;
})(Live2DCubismFramework$13 || (Live2DCubismFramework$13 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/motion/cubismmotion.ts
var EffectNameEyeBlink = "EyeBlink";
var EffectNameLipSync = "LipSync";
var TargetNameModel = "Model";
var TargetNameParameter = "Parameter";
var TargetNamePartOpacity = "PartOpacity";
var IdNameOpacity = "Opacity";
/**
* Cubism SDK R2 以前のモーションを再現させるなら true 、アニメータのモーションを正しく再現するなら false 。
*/
var UseOldBeziersCurveMotion = false;
function lerpPoints(a, b, t) {
	const result = new CubismMotionPoint();
	result.time = a.time + (b.time - a.time) * t;
	result.value = a.value + (b.value - a.value) * t;
	return result;
}
function linearEvaluate(points, time) {
	let t = (time - points[0].time) / (points[1].time - points[0].time);
	if (t < 0) t = 0;
	return points[0].value + (points[1].value - points[0].value) * t;
}
function bezierEvaluate(points, time) {
	let t = (time - points[0].time) / (points[3].time - points[0].time);
	if (t < 0) t = 0;
	const p01 = lerpPoints(points[0], points[1], t);
	const p12 = lerpPoints(points[1], points[2], t);
	const p23 = lerpPoints(points[2], points[3], t);
	return lerpPoints(lerpPoints(p01, p12, t), lerpPoints(p12, p23, t), t).value;
}
function bezierEvaluateCardanoInterpretation(points, time) {
	const x = time;
	const x1 = points[0].time;
	const x2 = points[3].time;
	const cx1 = points[1].time;
	const cx2 = points[2].time;
	const a = x2 - 3 * cx2 + 3 * cx1 - x1;
	const b = 3 * cx2 - 6 * cx1 + 3 * x1;
	const c = 3 * cx1 - 3 * x1;
	const d = x1 - x;
	const t = CubismMath.cardanoAlgorithmForBezier(a, b, c, d);
	const p01 = lerpPoints(points[0], points[1], t);
	const p12 = lerpPoints(points[1], points[2], t);
	const p23 = lerpPoints(points[2], points[3], t);
	return lerpPoints(lerpPoints(p01, p12, t), lerpPoints(p12, p23, t), t).value;
}
function steppedEvaluate(points, time) {
	return points[0].value;
}
function inverseSteppedEvaluate(points, time) {
	return points[1].value;
}
function evaluateCurve(motionData, index, time, isCorrection, endTime) {
	const curve = motionData.curves[index];
	let target = -1;
	const totalSegmentCount = curve.baseSegmentIndex + curve.segmentCount;
	let pointPosition = 0;
	for (let i = curve.baseSegmentIndex; i < totalSegmentCount; ++i) {
		pointPosition = motionData.segments[i].basePointIndex + (motionData.segments[i].segmentType == CubismMotionSegmentType.CubismMotionSegmentType_Bezier ? 3 : 1);
		if (motionData.points[pointPosition].time > time) {
			target = i;
			break;
		}
	}
	if (target == -1) {
		if (isCorrection && time < endTime) return correctEndPoint(motionData, totalSegmentCount - 1, motionData.segments[curve.baseSegmentIndex].basePointIndex, pointPosition, time, endTime);
		return motionData.points[pointPosition].value;
	}
	const segment = motionData.segments[target];
	return segment.evaluate(motionData.points.slice(segment.basePointIndex), time);
}
/**
* 終点から始点への補正処理
* @param motionData
* @param segmentIndex
* @param beginIndex
* @param endIndex
* @param time
* @param endTime
* @return
*/
function correctEndPoint(motionData, segmentIndex, beginIndex, endIndex, time, endTime) {
	const motionPoint = [new CubismMotionPoint(), new CubismMotionPoint()];
	{
		const src = motionData.points[endIndex];
		motionPoint[0].time = src.time;
		motionPoint[0].value = src.value;
	}
	{
		const src = motionData.points[beginIndex];
		motionPoint[1].time = endTime;
		motionPoint[1].value = src.value;
	}
	switch (motionData.segments[segmentIndex].segmentType) {
		case CubismMotionSegmentType.CubismMotionSegmentType_Linear:
		case CubismMotionSegmentType.CubismMotionSegmentType_Bezier:
		default: return linearEvaluate(motionPoint, time);
		case CubismMotionSegmentType.CubismMotionSegmentType_Stepped: return steppedEvaluate(motionPoint, time);
		case CubismMotionSegmentType.CubismMotionSegmentType_InverseStepped: return inverseSteppedEvaluate(motionPoint, time);
	}
}
/**
* モーションクラス
*
* モーションのクラス。
*/
var CubismMotion = class CubismMotion extends ACubismMotion {
	/**
	* インスタンスを作成する
	*
	* @param buffer motion3.jsonが読み込まれているバッファ
	* @param size バッファのサイズ
	* @param onFinishedMotionHandler モーション再生終了時に呼び出されるコールバック関数
	* @param onBeganMotionHandler モーション再生開始時に呼び出されるコールバック関数
	* @param shouldCheckMotionConsistency motion3.json整合性チェックするかどうか
	* @return 作成されたインスタンス
	*/
	static create(buffer, size, onFinishedMotionHandler, onBeganMotionHandler, shouldCheckMotionConsistency = false) {
		const ret = new CubismMotion();
		ret.parse(buffer, size, shouldCheckMotionConsistency);
		if (ret._motionData) {
			ret._sourceFrameRate = ret._motionData.fps;
			ret._loopDurationSeconds = ret._motionData.duration;
			ret._onFinishedMotion = onFinishedMotionHandler;
			ret._onBeganMotion = onBeganMotionHandler;
		} else {
			csmDelete(ret);
			return null;
		}
		return ret;
	}
	/**
	* モデルのパラメータの更新の実行
	* @param model             対象のモデル
	* @param userTimeSeconds   現在の時刻[秒]
	* @param fadeWeight        モーションの重み
	* @param motionQueueEntry  CubismMotionQueueManagerで管理されているモーション
	*/
	doUpdateParameters(model, userTimeSeconds, fadeWeight, motionQueueEntry) {
		if (this._modelCurveIdEyeBlink == null) this._modelCurveIdEyeBlink = CubismFramework.getIdManager().getId(EffectNameEyeBlink);
		if (this._modelCurveIdLipSync == null) this._modelCurveIdLipSync = CubismFramework.getIdManager().getId(EffectNameLipSync);
		if (this._modelCurveIdOpacity == null) this._modelCurveIdOpacity = CubismFramework.getIdManager().getId(IdNameOpacity);
		if (this._motionBehavior === 1) {
			if (this._previousLoopState !== this._isLoop) {
				this.adjustEndTime(motionQueueEntry);
				this._previousLoopState = this._isLoop;
			}
		}
		let timeOffsetSeconds = userTimeSeconds - motionQueueEntry.getStartTime();
		if (timeOffsetSeconds < 0) timeOffsetSeconds = 0;
		let lipSyncValue = Number.MAX_VALUE;
		let eyeBlinkValue = Number.MAX_VALUE;
		const maxTargetSize = 64;
		let lipSyncFlags = 0;
		let eyeBlinkFlags = 0;
		if (this._eyeBlinkParameterIds.length > maxTargetSize) CubismLogDebug("too many eye blink targets : {0}", this._eyeBlinkParameterIds.length);
		if (this._lipSyncParameterIds.length > maxTargetSize) CubismLogDebug("too many lip sync targets : {0}", this._lipSyncParameterIds.length);
		const tmpFadeIn = this._fadeInSeconds <= 0 ? 1 : CubismMath.getEasingSine((userTimeSeconds - motionQueueEntry.getFadeInStartTime()) / this._fadeInSeconds);
		const tmpFadeOut = this._fadeOutSeconds <= 0 || motionQueueEntry.getEndTime() < 0 ? 1 : CubismMath.getEasingSine((motionQueueEntry.getEndTime() - userTimeSeconds) / this._fadeOutSeconds);
		let value;
		let c, parameterIndex;
		let time = timeOffsetSeconds;
		let duration = this._motionData.duration;
		const isCorrection = this._motionBehavior === 1 && this._isLoop;
		if (this._isLoop) {
			if (this._motionBehavior === 1) duration += 1 / this._motionData.fps;
			while (time > duration) time -= duration;
		}
		const curves = this._motionData.curves;
		for (c = 0; c < this._motionData.curveCount && curves[c].type == CubismMotionCurveTarget.CubismMotionCurveTarget_Model; ++c) {
			value = evaluateCurve(this._motionData, c, time, isCorrection, duration);
			if (curves[c].id == this._modelCurveIdEyeBlink) eyeBlinkValue = value;
			else if (curves[c].id == this._modelCurveIdLipSync) lipSyncValue = value;
			else if (curves[c].id == this._modelCurveIdOpacity) {
				this._modelOpacity = value;
				model.setModelOapcity(this.getModelOpacityValue());
			}
		}
		let parameterMotionCurveCount = 0;
		for (; c < this._motionData.curveCount && curves[c].type == CubismMotionCurveTarget.CubismMotionCurveTarget_Parameter; ++c) {
			parameterMotionCurveCount++;
			parameterIndex = model.getParameterIndex(curves[c].id);
			if (parameterIndex == -1) continue;
			const sourceValue = model.getParameterValueByIndex(parameterIndex);
			value = evaluateCurve(this._motionData, c, time, isCorrection, duration);
			if (eyeBlinkValue != Number.MAX_VALUE) {
				for (let i = 0; i < this._eyeBlinkParameterIds.length && i < maxTargetSize; ++i) if (this._eyeBlinkParameterIds[i] == curves[c].id) {
					value *= eyeBlinkValue;
					eyeBlinkFlags |= 1 << i;
					break;
				}
			}
			if (lipSyncValue != Number.MAX_VALUE) {
				for (let i = 0; i < this._lipSyncParameterIds.length && i < maxTargetSize; ++i) if (this._lipSyncParameterIds[i] == curves[c].id) {
					value += lipSyncValue;
					lipSyncFlags |= 1 << i;
					break;
				}
			}
			if (model.isRepeat(parameterIndex)) value = model.getParameterRepeatValue(parameterIndex, value);
			let v;
			if (curves[c].fadeInTime < 0 && curves[c].fadeOutTime < 0) v = sourceValue + (value - sourceValue) * fadeWeight;
			else {
				let fin;
				let fout;
				if (curves[c].fadeInTime < 0) fin = tmpFadeIn;
				else fin = curves[c].fadeInTime == 0 ? 1 : CubismMath.getEasingSine((userTimeSeconds - motionQueueEntry.getFadeInStartTime()) / curves[c].fadeInTime);
				if (curves[c].fadeOutTime < 0) fout = tmpFadeOut;
				else fout = curves[c].fadeOutTime == 0 || motionQueueEntry.getEndTime() < 0 ? 1 : CubismMath.getEasingSine((motionQueueEntry.getEndTime() - userTimeSeconds) / curves[c].fadeOutTime);
				const paramWeight = this._weight * fin * fout;
				v = sourceValue + (value - sourceValue) * paramWeight;
			}
			model.setParameterValueByIndex(parameterIndex, v, 1);
		}
		if (eyeBlinkValue != Number.MAX_VALUE) for (let i = 0; i < this._eyeBlinkParameterIds.length && i < maxTargetSize; ++i) {
			const sourceValue = model.getParameterValueById(this._eyeBlinkParameterIds[i]);
			if (eyeBlinkFlags >> i & 1) continue;
			const v = sourceValue + (eyeBlinkValue - sourceValue) * fadeWeight;
			model.setParameterValueById(this._eyeBlinkParameterIds[i], v);
		}
		if (lipSyncValue != Number.MAX_VALUE) for (let i = 0; i < this._lipSyncParameterIds.length && i < maxTargetSize; ++i) {
			const sourceValue = model.getParameterValueById(this._lipSyncParameterIds[i]);
			if (lipSyncFlags >> i & 1) continue;
			const v = sourceValue + (lipSyncValue - sourceValue) * fadeWeight;
			model.setParameterValueById(this._lipSyncParameterIds[i], v);
		}
		for (; c < this._motionData.curveCount && curves[c].type == CubismMotionCurveTarget.CubismMotionCurveTarget_PartOpacity; ++c) {
			parameterIndex = model.getParameterIndex(curves[c].id);
			if (parameterIndex == -1) continue;
			value = evaluateCurve(this._motionData, c, time, isCorrection, duration);
			model.setParameterValueByIndex(parameterIndex, value);
		}
		if (timeOffsetSeconds >= duration) {
			if (this._isLoop) this.updateForNextLoop(motionQueueEntry, userTimeSeconds, time);
			else {
				if (this._onFinishedMotion) this._onFinishedMotion(this);
				motionQueueEntry.setIsFinished(true);
			}
		}
		this._lastWeight = fadeWeight;
	}
	/**
	* Sets the version of the Motion Behavior.
	*
	* @param Specifies the version of the Motion Behavior.
	*/
	setMotionBehavior(motionBehavior) {
		this._motionBehavior = motionBehavior;
	}
	/**
	* Gets the version of the Motion Behavior.
	*
	* @return Returns the version of the Motion Behavior.
	*/
	getMotionBehavior() {
		return this._motionBehavior;
	}
	/**
	* モーションの長さを取得する。
	*
	* @return  モーションの長さ[秒]
	*/
	getDuration() {
		return this._isLoop ? -1 : this._loopDurationSeconds;
	}
	/**
	* モーションのループ時の長さを取得する。
	*
	* @return  モーションのループ時の長さ[秒]
	*/
	getLoopDuration() {
		return this._loopDurationSeconds;
	}
	/**
	* パラメータに対するフェードインの時間を設定する。
	*
	* @param parameterId     パラメータID
	* @param value           フェードインにかかる時間[秒]
	*/
	setParameterFadeInTime(parameterId, value) {
		const curves = this._motionData.curves;
		for (let i = 0; i < this._motionData.curveCount; ++i) if (parameterId == curves[i].id) {
			curves[i].fadeInTime = value;
			return;
		}
	}
	/**
	* パラメータに対するフェードアウトの時間の設定
	* @param parameterId     パラメータID
	* @param value           フェードアウトにかかる時間[秒]
	*/
	setParameterFadeOutTime(parameterId, value) {
		const curves = this._motionData.curves;
		for (let i = 0; i < this._motionData.curveCount; ++i) if (parameterId == curves[i].id) {
			curves[i].fadeOutTime = value;
			return;
		}
	}
	/**
	* パラメータに対するフェードインの時間の取得
	* @param    parameterId     パラメータID
	* @return   フェードインにかかる時間[秒]
	*/
	getParameterFadeInTime(parameterId) {
		const curves = this._motionData.curves;
		for (let i = 0; i < this._motionData.curveCount; ++i) if (parameterId == curves[i].id) return curves[i].fadeInTime;
		return -1;
	}
	/**
	* パラメータに対するフェードアウトの時間を取得
	*
	* @param   parameterId     パラメータID
	* @return   フェードアウトにかかる時間[秒]
	*/
	getParameterFadeOutTime(parameterId) {
		const curves = this._motionData.curves;
		for (let i = 0; i < this._motionData.curveCount; ++i) if (parameterId == curves[i].id) return curves[i].fadeOutTime;
		return -1;
	}
	/**
	* 自動エフェクトがかかっているパラメータIDリストの設定
	* @param eyeBlinkParameterIds    自動まばたきがかかっているパラメータIDのリスト
	* @param lipSyncParameterIds     リップシンクがかかっているパラメータIDのリスト
	*/
	setEffectIds(eyeBlinkParameterIds, lipSyncParameterIds) {
		this._eyeBlinkParameterIds = eyeBlinkParameterIds;
		this._lipSyncParameterIds = lipSyncParameterIds;
	}
	/**
	* コンストラクタ
	*/
	constructor() {
		super();
		this._motionBehavior = 1;
		this._sourceFrameRate = 30;
		this._loopDurationSeconds = -1;
		this._isLoop = false;
		this._isLoopFadeIn = true;
		this._lastWeight = 0;
		this._motionData = null;
		this._modelCurveIdEyeBlink = null;
		this._modelCurveIdLipSync = null;
		this._modelCurveIdOpacity = null;
		this._eyeBlinkParameterIds = null;
		this._lipSyncParameterIds = null;
		this._modelOpacity = 1;
		this._debugMode = false;
	}
	/**
	* デストラクタ相当の処理
	*/
	release() {
		this._motionData = void 0;
		this._motionData = null;
	}
	/**
	*
	* @param motionQueueEntry
	* @param userTimeSeconds
	* @param time
	*/
	updateForNextLoop(motionQueueEntry, userTimeSeconds, time) {
		switch (this._motionBehavior) {
			case 1:
			default:
				motionQueueEntry.setStartTime(userTimeSeconds - time);
				if (this._isLoopFadeIn) motionQueueEntry.setFadeInStartTime(userTimeSeconds - time);
				if (this._onFinishedMotion != null) this._onFinishedMotion(this);
				break;
			case 0:
				motionQueueEntry.setStartTime(userTimeSeconds);
				if (this._isLoopFadeIn) motionQueueEntry.setFadeInStartTime(userTimeSeconds);
		}
	}
	/**
	* motion3.jsonをパースする。
	*
	* @param motionJson  motion3.jsonが読み込まれているバッファ
	* @param size        バッファのサイズ
	* @param shouldCheckMotionConsistency motion3.json整合性チェックするかどうか
	*/
	parse(motionJson, size, shouldCheckMotionConsistency = false) {
		let json = new CubismMotionJson(motionJson, size);
		if (!json) {
			json.release();
			json = void 0;
			return;
		}
		if (shouldCheckMotionConsistency) {
			if (!json.hasConsistency()) {
				json.release();
				CubismLogError("Inconsistent motion3.json.");
				return;
			}
		}
		this._motionData = new CubismMotionData();
		this._motionData.duration = json.getMotionDuration();
		this._motionData.loop = json.isMotionLoop();
		this._motionData.curveCount = json.getMotionCurveCount();
		this._motionData.fps = json.getMotionFps();
		this._motionData.eventCount = json.getEventCount();
		const areBeziersRestructed = json.getEvaluationOptionFlag(EvaluationOptionFlag.EvaluationOptionFlag_AreBeziersRistricted);
		if (json.isExistMotionFadeInTime()) this._fadeInSeconds = json.getMotionFadeInTime() < 0 ? 1 : json.getMotionFadeInTime();
		else this._fadeInSeconds = 1;
		if (json.isExistMotionFadeOutTime()) this._fadeOutSeconds = json.getMotionFadeOutTime() < 0 ? 1 : json.getMotionFadeOutTime();
		else this._fadeOutSeconds = 1;
		updateSize(this._motionData.curves, this._motionData.curveCount, CubismMotionCurve, true);
		updateSize(this._motionData.segments, json.getMotionTotalSegmentCount(), CubismMotionSegment, true);
		updateSize(this._motionData.points, json.getMotionTotalPointCount(), CubismMotionPoint, true);
		updateSize(this._motionData.events, this._motionData.eventCount, CubismMotionEvent, true);
		let totalPointCount = 0;
		let totalSegmentCount = 0;
		for (let curveCount = 0; curveCount < this._motionData.curveCount; ++curveCount) {
			if (json.getMotionCurveTarget(curveCount) == TargetNameModel) this._motionData.curves[curveCount].type = CubismMotionCurveTarget.CubismMotionCurveTarget_Model;
			else if (json.getMotionCurveTarget(curveCount) == TargetNameParameter) this._motionData.curves[curveCount].type = CubismMotionCurveTarget.CubismMotionCurveTarget_Parameter;
			else if (json.getMotionCurveTarget(curveCount) == TargetNamePartOpacity) this._motionData.curves[curveCount].type = CubismMotionCurveTarget.CubismMotionCurveTarget_PartOpacity;
			else CubismLogWarning("Warning : Unable to get segment type from Curve! The number of \"CurveCount\" may be incorrect!");
			this._motionData.curves[curveCount].id = json.getMotionCurveId(curveCount);
			this._motionData.curves[curveCount].baseSegmentIndex = totalSegmentCount;
			this._motionData.curves[curveCount].fadeInTime = json.isExistMotionCurveFadeInTime(curveCount) ? json.getMotionCurveFadeInTime(curveCount) : -1;
			this._motionData.curves[curveCount].fadeOutTime = json.isExistMotionCurveFadeOutTime(curveCount) ? json.getMotionCurveFadeOutTime(curveCount) : -1;
			for (let segmentPosition = 0; segmentPosition < json.getMotionCurveSegmentCount(curveCount);) {
				if (segmentPosition == 0) {
					this._motionData.segments[totalSegmentCount].basePointIndex = totalPointCount;
					this._motionData.points[totalPointCount].time = json.getMotionCurveSegment(curveCount, segmentPosition);
					this._motionData.points[totalPointCount].value = json.getMotionCurveSegment(curveCount, segmentPosition + 1);
					totalPointCount += 1;
					segmentPosition += 2;
				} else this._motionData.segments[totalSegmentCount].basePointIndex = totalPointCount - 1;
				switch (json.getMotionCurveSegment(curveCount, segmentPosition)) {
					case CubismMotionSegmentType.CubismMotionSegmentType_Linear:
						this._motionData.segments[totalSegmentCount].segmentType = CubismMotionSegmentType.CubismMotionSegmentType_Linear;
						this._motionData.segments[totalSegmentCount].evaluate = linearEvaluate;
						this._motionData.points[totalPointCount].time = json.getMotionCurveSegment(curveCount, segmentPosition + 1);
						this._motionData.points[totalPointCount].value = json.getMotionCurveSegment(curveCount, segmentPosition + 2);
						totalPointCount += 1;
						segmentPosition += 3;
						break;
					case CubismMotionSegmentType.CubismMotionSegmentType_Bezier:
						this._motionData.segments[totalSegmentCount].segmentType = CubismMotionSegmentType.CubismMotionSegmentType_Bezier;
						if (areBeziersRestructed || UseOldBeziersCurveMotion) this._motionData.segments[totalSegmentCount].evaluate = bezierEvaluate;
						else this._motionData.segments[totalSegmentCount].evaluate = bezierEvaluateCardanoInterpretation;
						this._motionData.points[totalPointCount].time = json.getMotionCurveSegment(curveCount, segmentPosition + 1);
						this._motionData.points[totalPointCount].value = json.getMotionCurveSegment(curveCount, segmentPosition + 2);
						this._motionData.points[totalPointCount + 1].time = json.getMotionCurveSegment(curveCount, segmentPosition + 3);
						this._motionData.points[totalPointCount + 1].value = json.getMotionCurveSegment(curveCount, segmentPosition + 4);
						this._motionData.points[totalPointCount + 2].time = json.getMotionCurveSegment(curveCount, segmentPosition + 5);
						this._motionData.points[totalPointCount + 2].value = json.getMotionCurveSegment(curveCount, segmentPosition + 6);
						totalPointCount += 3;
						segmentPosition += 7;
						break;
					case CubismMotionSegmentType.CubismMotionSegmentType_Stepped:
						this._motionData.segments[totalSegmentCount].segmentType = CubismMotionSegmentType.CubismMotionSegmentType_Stepped;
						this._motionData.segments[totalSegmentCount].evaluate = steppedEvaluate;
						this._motionData.points[totalPointCount].time = json.getMotionCurveSegment(curveCount, segmentPosition + 1);
						this._motionData.points[totalPointCount].value = json.getMotionCurveSegment(curveCount, segmentPosition + 2);
						totalPointCount += 1;
						segmentPosition += 3;
						break;
					case CubismMotionSegmentType.CubismMotionSegmentType_InverseStepped:
						this._motionData.segments[totalSegmentCount].segmentType = CubismMotionSegmentType.CubismMotionSegmentType_InverseStepped;
						this._motionData.segments[totalSegmentCount].evaluate = inverseSteppedEvaluate;
						this._motionData.points[totalPointCount].time = json.getMotionCurveSegment(curveCount, segmentPosition + 1);
						this._motionData.points[totalPointCount].value = json.getMotionCurveSegment(curveCount, segmentPosition + 2);
						totalPointCount += 1;
						segmentPosition += 3;
						break;
					default: CSM_ASSERT(0);
				}
				++this._motionData.curves[curveCount].segmentCount;
				++totalSegmentCount;
			}
		}
		for (let userdatacount = 0; userdatacount < json.getEventCount(); ++userdatacount) {
			this._motionData.events[userdatacount].fireTime = json.getEventTime(userdatacount);
			this._motionData.events[userdatacount].value = json.getEventValue(userdatacount);
		}
		json.release();
		json = void 0;
		json = null;
	}
	/**
	* モデルのパラメータ更新
	*
	* イベント発火のチェック。
	* 入力する時間は呼ばれるモーションタイミングを０とした秒数で行う。
	*
	* @param beforeCheckTimeSeconds   前回のイベントチェック時間[秒]
	* @param motionTimeSeconds        今回の再生時間[秒]
	*/
	getFiredEvent(beforeCheckTimeSeconds, motionTimeSeconds) {
		updateSize(this._firedEventValues, 0);
		for (let u = 0; u < this._motionData.eventCount; ++u) if (this._motionData.events[u].fireTime > beforeCheckTimeSeconds && this._motionData.events[u].fireTime <= motionTimeSeconds) this._firedEventValues.push(this._motionData.events[u].value);
		return this._firedEventValues;
	}
	/**
	* 透明度のカーブが存在するかどうかを確認する
	*
	* @return true  -> キーが存在する
	*          false -> キーが存在しない
	*/
	isExistModelOpacity() {
		for (let i = 0; i < this._motionData.curveCount; i++) {
			const curve = this._motionData.curves[i];
			if (curve.type != CubismMotionCurveTarget.CubismMotionCurveTarget_Model) continue;
			if (curve.id.getString().localeCompare(IdNameOpacity) == 0) return true;
		}
		return false;
	}
	/**
	* 透明度のカーブのインデックスを返す
	*
	* @return success:透明度のカーブのインデックス
	*/
	getModelOpacityIndex() {
		if (this.isExistModelOpacity()) for (let i = 0; i < this._motionData.curveCount; i++) {
			const curve = this._motionData.curves[i];
			if (curve.type != CubismMotionCurveTarget.CubismMotionCurveTarget_Model) continue;
			if (curve.id.getString().localeCompare(IdNameOpacity) == 0) return i;
		}
		return -1;
	}
	/**
	* 透明度のIdを返す
	*
	* @param index モーションカーブのインデックス
	* @return success:透明度のカーブのインデックス
	*/
	getModelOpacityId(index) {
		if (index != -1) {
			const curve = this._motionData.curves[index];
			if (curve.type == CubismMotionCurveTarget.CubismMotionCurveTarget_Model) {
				if (curve.id.getString().localeCompare(IdNameOpacity) == 0) return CubismFramework.getIdManager().getId(curve.id.getString());
			}
		}
		return null;
	}
	/**
	* 現在時間の透明度の値を返す
	*
	* @return success:モーションの当該時間におけるOpacityの値
	*/
	getModelOpacityValue() {
		return this._modelOpacity;
	}
	/**
	* デバッグ用フラグを設定する
	*
	* @param debugMode デバッグモードの有効・無効
	*/
	setDebugMode(debugMode) {
		this._debugMode = debugMode;
	}
};
var Live2DCubismFramework$12;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismMotion = CubismMotion;
})(Live2DCubismFramework$12 || (Live2DCubismFramework$12 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/motion/cubismmotionmanager.ts
/**
* モーションの管理
*
* モーションの管理を行うクラス
*/
var CubismMotionManager = class extends CubismMotionQueueManager {
	/**
	* コンストラクタ
	*/
	constructor() {
		super();
		this._currentPriority = 0;
		this._reservePriority = 0;
	}
	/**
	* 再生中のモーションの優先度の取得
	* @return  モーションの優先度
	*/
	getCurrentPriority() {
		return this._currentPriority;
	}
	/**
	* 予約中のモーションの優先度を取得する。
	* @return  モーションの優先度
	*/
	getReservePriority() {
		return this._reservePriority;
	}
	/**
	* 予約中のモーションの優先度を設定する。
	* @param   val     優先度
	*/
	setReservePriority(val) {
		this._reservePriority = val;
	}
	/**
	* 優先度を設定してモーションを開始する。
	*
	* @param motion          モーション
	* @param autoDelete      再生が狩猟したモーションのインスタンスを削除するならtrue
	* @param priority        優先度
	* @return                開始したモーションの識別番号を返す。個別のモーションが終了したか否かを判定するIsFinished()の引数で使用する。開始できない時は「-1」
	*/
	startMotionPriority(motion, autoDelete, priority) {
		if (priority == this._reservePriority) this._reservePriority = 0;
		this._currentPriority = priority;
		return super.startMotion(motion, autoDelete);
	}
	/**
	* モーションを更新して、モデルにパラメータ値を反映する。
	*
	* @param model   対象のモデル
	* @param deltaTimeSeconds    デルタ時間[秒]
	* @return  true    更新されている
	* @return  false   更新されていない
	*/
	updateMotion(model, deltaTimeSeconds) {
		this._userTimeSeconds += deltaTimeSeconds;
		const updated = super.doUpdateMotion(model, this._userTimeSeconds);
		if (this.isFinished()) this._currentPriority = 0;
		return updated;
	}
	/**
	* モーションを予約する。
	*
	* @param   priority    優先度
	* @return  true    予約できた
	* @return  false   予約できなかった
	*/
	reserveMotion(priority) {
		if (priority <= this._reservePriority || priority <= this._currentPriority) return false;
		this._reservePriority = priority;
		return true;
	}
};
var Live2DCubismFramework$11;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismMotionManager = CubismMotionManager;
})(Live2DCubismFramework$11 || (Live2DCubismFramework$11 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/physics/cubismphysicsinternal.ts
/**
* 物理演算の適用先の種類
*/
var CubismPhysicsTargetType = /* @__PURE__ */ function(CubismPhysicsTargetType) {
	CubismPhysicsTargetType[CubismPhysicsTargetType["CubismPhysicsTargetType_Parameter"] = 0] = "CubismPhysicsTargetType_Parameter";
	return CubismPhysicsTargetType;
}({});
/**
* 物理演算の入力の種類
*/
var CubismPhysicsSource = /* @__PURE__ */ function(CubismPhysicsSource) {
	CubismPhysicsSource[CubismPhysicsSource["CubismPhysicsSource_X"] = 0] = "CubismPhysicsSource_X";
	CubismPhysicsSource[CubismPhysicsSource["CubismPhysicsSource_Y"] = 1] = "CubismPhysicsSource_Y";
	CubismPhysicsSource[CubismPhysicsSource["CubismPhysicsSource_Angle"] = 2] = "CubismPhysicsSource_Angle";
	return CubismPhysicsSource;
}({});
/**
* @brief 物理演算で使用する外部の力
*
* 物理演算で使用する外部の力。
*/
var PhysicsJsonEffectiveForces = class {
	constructor() {
		this.gravity = new CubismVector2(0, 0);
		this.wind = new CubismVector2(0, 0);
	}
};
/**
* 物理演算のパラメータ情報
*/
var CubismPhysicsParameter = class {};
/**
* 物理演算の正規化情報
*/
var CubismPhysicsNormalization = class {};
/**
* 物理演算の演算委使用する物理点の情報
*/
var CubismPhysicsParticle = class {
	constructor() {
		this.initialPosition = new CubismVector2(0, 0);
		this.position = new CubismVector2(0, 0);
		this.lastPosition = new CubismVector2(0, 0);
		this.lastGravity = new CubismVector2(0, 0);
		this.force = new CubismVector2(0, 0);
		this.velocity = new CubismVector2(0, 0);
	}
};
/**
* 物理演算の物理点の管理
*/
var CubismPhysicsSubRig = class {
	constructor() {
		this.normalizationPosition = new CubismPhysicsNormalization();
		this.normalizationAngle = new CubismPhysicsNormalization();
	}
};
/**
* 物理演算の入力情報
*/
var CubismPhysicsInput = class {
	constructor() {
		this.source = new CubismPhysicsParameter();
	}
};
/**
* @brief 物理演算の出力情報
*
* 物理演算の出力情報。
*/
var CubismPhysicsOutput = class {
	constructor() {
		this.destination = new CubismPhysicsParameter();
		this.translationScale = new CubismVector2(0, 0);
	}
};
/**
* @brief 物理演算のデータ
*
* 物理演算のデータ。
*/
var CubismPhysicsRig = class {
	constructor() {
		this.settings = new Array();
		this.inputs = new Array();
		this.outputs = new Array();
		this.particles = new Array();
		this.gravity = new CubismVector2(0, 0);
		this.wind = new CubismVector2(0, 0);
		this.fps = 0;
	}
};
var Live2DCubismFramework$10;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismPhysicsInput = CubismPhysicsInput;
	_Live2DCubismFramework.CubismPhysicsNormalization = CubismPhysicsNormalization;
	_Live2DCubismFramework.CubismPhysicsOutput = CubismPhysicsOutput;
	_Live2DCubismFramework.CubismPhysicsParameter = CubismPhysicsParameter;
	_Live2DCubismFramework.CubismPhysicsParticle = CubismPhysicsParticle;
	_Live2DCubismFramework.CubismPhysicsRig = CubismPhysicsRig;
	_Live2DCubismFramework.CubismPhysicsSource = CubismPhysicsSource;
	_Live2DCubismFramework.CubismPhysicsSubRig = CubismPhysicsSubRig;
	_Live2DCubismFramework.CubismPhysicsTargetType = CubismPhysicsTargetType;
	_Live2DCubismFramework.PhysicsJsonEffectiveForces = PhysicsJsonEffectiveForces;
})(Live2DCubismFramework$10 || (Live2DCubismFramework$10 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/physics/cubismphysicsjson.ts
var Position = "Position";
var X = "X";
var Y = "Y";
var Angle = "Angle";
var Type = "Type";
var Id$1 = "Id";
var Meta$1 = "Meta";
var EffectiveForces = "EffectiveForces";
var TotalInputCount = "TotalInputCount";
var TotalOutputCount = "TotalOutputCount";
var PhysicsSettingCount = "PhysicsSettingCount";
var Gravity = "Gravity";
var Wind = "Wind";
var VertexCount = "VertexCount";
var Fps = "Fps";
var PhysicsSettings = "PhysicsSettings";
var Normalization = "Normalization";
var Minimum = "Minimum";
var Maximum = "Maximum";
var Default = "Default";
var Reflect = "Reflect";
var Weight = "Weight";
var Input = "Input";
var Source = "Source";
var Output = "Output";
var Scale = "Scale";
var VertexIndex = "VertexIndex";
var Destination = "Destination";
var Vertices = "Vertices";
var Mobility = "Mobility";
var Delay = "Delay";
var Radius = "Radius";
var Acceleration = "Acceleration";
/**
* physics3.jsonのコンテナ。
*/
var CubismPhysicsJson = class {
	/**
	* コンストラクタ
	* @param buffer physics3.jsonが読み込まれているバッファ
	* @param size バッファのサイズ
	*/
	constructor(buffer, size) {
		this._json = CubismJson.create(buffer, size);
	}
	/**
	* デストラクタ相当の処理
	*/
	release() {
		CubismJson.delete(this._json);
	}
	/**
	* 重力の取得
	* @return 重力
	*/
	getGravity() {
		const ret = new CubismVector2(0, 0);
		ret.x = this._json.getRoot().getValueByString(Meta$1).getValueByString(EffectiveForces).getValueByString(Gravity).getValueByString(X).toFloat();
		ret.y = this._json.getRoot().getValueByString(Meta$1).getValueByString(EffectiveForces).getValueByString(Gravity).getValueByString(Y).toFloat();
		return ret;
	}
	/**
	* 風の取得
	* @return 風
	*/
	getWind() {
		const ret = new CubismVector2(0, 0);
		ret.x = this._json.getRoot().getValueByString(Meta$1).getValueByString(EffectiveForces).getValueByString(Wind).getValueByString(X).toFloat();
		ret.y = this._json.getRoot().getValueByString(Meta$1).getValueByString(EffectiveForces).getValueByString(Wind).getValueByString(Y).toFloat();
		return ret;
	}
	/**
	* 物理演算設定FPSの取得
	* @return 物理演算設定FPS
	*/
	getFps() {
		return this._json.getRoot().getValueByString(Meta$1).getValueByString(Fps).toFloat(0);
	}
	/**
	* 物理店の管理の個数の取得
	* @return 物理店の管理の個数
	*/
	getSubRigCount() {
		return this._json.getRoot().getValueByString(Meta$1).getValueByString(PhysicsSettingCount).toInt();
	}
	/**
	* 入力の総合計の取得
	* @return 入力の総合計
	*/
	getTotalInputCount() {
		return this._json.getRoot().getValueByString(Meta$1).getValueByString(TotalInputCount).toInt();
	}
	/**
	* 出力の総合計の取得
	* @return 出力の総合計
	*/
	getTotalOutputCount() {
		return this._json.getRoot().getValueByString(Meta$1).getValueByString(TotalOutputCount).toInt();
	}
	/**
	* 物理点の個数の取得
	* @return 物理点の個数
	*/
	getVertexCount() {
		return this._json.getRoot().getValueByString(Meta$1).getValueByString(VertexCount).toInt();
	}
	/**
	* 正規化された位置の最小値の取得
	* @param physicsSettingIndex 物理演算の設定のインデックス
	* @return 正規化された位置の最小値
	*/
	getNormalizationPositionMinimumValue(physicsSettingIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Normalization).getValueByString(Position).getValueByString(Minimum).toFloat();
	}
	/**
	* 正規化された位置の最大値の取得
	* @param physicsSettingIndex 物理演算の設定のインデックス
	* @return 正規化された位置の最大値
	*/
	getNormalizationPositionMaximumValue(physicsSettingIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Normalization).getValueByString(Position).getValueByString(Maximum).toFloat();
	}
	/**
	* 正規化された位置のデフォルト値の取得
	* @param physicsSettingIndex 物理演算の設定のインデックス
	* @return 正規化された位置のデフォルト値
	*/
	getNormalizationPositionDefaultValue(physicsSettingIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Normalization).getValueByString(Position).getValueByString(Default).toFloat();
	}
	/**
	* 正規化された角度の最小値の取得
	* @param physicsSettingIndex 物理演算の設定のインデックス
	* @return 正規化された角度の最小値
	*/
	getNormalizationAngleMinimumValue(physicsSettingIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Normalization).getValueByString(Angle).getValueByString(Minimum).toFloat();
	}
	/**
	* 正規化された角度の最大値の取得
	* @param physicsSettingIndex
	* @return 正規化された角度の最大値
	*/
	getNormalizationAngleMaximumValue(physicsSettingIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Normalization).getValueByString(Angle).getValueByString(Maximum).toFloat();
	}
	/**
	* 正規化された角度のデフォルト値の取得
	* @param physicsSettingIndex 物理演算の設定のインデックス
	* @return 正規化された角度のデフォルト値
	*/
	getNormalizationAngleDefaultValue(physicsSettingIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Normalization).getValueByString(Angle).getValueByString(Default).toFloat();
	}
	/**
	* 入力の個数の取得
	* @param physicsSettingIndex 物理演算の設定のインデックス
	* @return 入力の個数
	*/
	getInputCount(physicsSettingIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Input).getVector().length;
	}
	/**
	* 入力の重みの取得
	* @param physicsSettingIndex 物理演算の設定のインデックス
	* @param inputIndex 入力のインデックス
	* @return 入力の重み
	*/
	getInputWeight(physicsSettingIndex, inputIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Input).getValueByIndex(inputIndex).getValueByString(Weight).toFloat();
	}
	/**
	* 入力の反転の取得
	* @param physicsSettingIndex 物理演算の設定のインデックス
	* @param inputIndex 入力のインデックス
	* @return 入力の反転
	*/
	getInputReflect(physicsSettingIndex, inputIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Input).getValueByIndex(inputIndex).getValueByString(Reflect).toBoolean();
	}
	/**
	* 入力の種類の取得
	* @param physicsSettingIndex 物理演算の設定のインデックス
	* @param inputIndex 入力のインデックス
	* @return 入力の種類
	*/
	getInputType(physicsSettingIndex, inputIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Input).getValueByIndex(inputIndex).getValueByString(Type).getRawString();
	}
	/**
	* 入力元のIDの取得
	* @param physicsSettingIndex 物理演算の設定のインデックス
	* @param inputIndex 入力のインデックス
	* @return 入力元のID
	*/
	getInputSourceId(physicsSettingIndex, inputIndex) {
		return CubismFramework.getIdManager().getId(this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Input).getValueByIndex(inputIndex).getValueByString(Source).getValueByString(Id$1).getRawString());
	}
	/**
	* 出力の個数の取得
	* @param physicsSettingIndex 物理演算の設定のインデックス
	* @return 出力の個数
	*/
	getOutputCount(physicsSettingIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Output).getVector().length;
	}
	/**
	* 出力の物理点のインデックスの取得
	* @param physicsSettingIndex 物理演算の設定のインデックス
	* @param outputIndex 出力のインデックス
	* @return 出力の物理点のインデックス
	*/
	getOutputVertexIndex(physicsSettingIndex, outputIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Output).getValueByIndex(outputIndex).getValueByString(VertexIndex).toInt();
	}
	/**
	* 出力の角度のスケールを取得する
	* @param physicsSettingIndex 物理演算の設定のインデックス
	* @param outputIndex 出力のインデックス
	* @return 出力の角度のスケール
	*/
	getOutputAngleScale(physicsSettingIndex, outputIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Output).getValueByIndex(outputIndex).getValueByString(Scale).toFloat();
	}
	/**
	* 出力の重みの取得
	* @param physicsSettingIndex 物理演算の設定のインデックス
	* @param outputIndex 出力のインデックス
	* @return 出力の重み
	*/
	getOutputWeight(physicsSettingIndex, outputIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Output).getValueByIndex(outputIndex).getValueByString(Weight).toFloat();
	}
	/**
	* 出力先のIDの取得
	* @param physicsSettingIndex 物理演算の設定のインデックス
	* @param outputIndex 出力のインデックス
	* @return 出力先のID
	*/
	getOutputDestinationId(physicsSettingIndex, outputIndex) {
		return CubismFramework.getIdManager().getId(this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Output).getValueByIndex(outputIndex).getValueByString(Destination).getValueByString(Id$1).getRawString());
	}
	/**
	* 出力の種類の取得
	* @param physicsSettingIndex 物理演算の設定のインデックス
	* @param outputIndex 出力のインデックス
	* @return 出力の種類
	*/
	getOutputType(physicsSettingIndex, outputIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Output).getValueByIndex(outputIndex).getValueByString(Type).getRawString();
	}
	/**
	* 出力の反転の取得
	* @param physicsSettingIndex 物理演算のインデックス
	* @param outputIndex 出力のインデックス
	* @return 出力の反転
	*/
	getOutputReflect(physicsSettingIndex, outputIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Output).getValueByIndex(outputIndex).getValueByString(Reflect).toBoolean();
	}
	/**
	* 物理点の個数の取得
	* @param physicsSettingIndex 物理演算男設定のインデックス
	* @return 物理点の個数
	*/
	getParticleCount(physicsSettingIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Vertices).getVector().length;
	}
	/**
	* 物理点の動きやすさの取得
	* @param physicsSettingIndex 物理演算の設定のインデックス
	* @param vertexIndex 物理点のインデックス
	* @return 物理点の動きやすさ
	*/
	getParticleMobility(physicsSettingIndex, vertexIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Vertices).getValueByIndex(vertexIndex).getValueByString(Mobility).toFloat();
	}
	/**
	* 物理点の遅れの取得
	* @param physicsSettingIndex 物理演算の設定のインデックス
	* @param vertexIndex 物理点のインデックス
	* @return 物理点の遅れ
	*/
	getParticleDelay(physicsSettingIndex, vertexIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Vertices).getValueByIndex(vertexIndex).getValueByString(Delay).toFloat();
	}
	/**
	* 物理点の加速度の取得
	* @param physicsSettingIndex 物理演算の設定
	* @param vertexIndex 物理点のインデックス
	* @return 物理点の加速度
	*/
	getParticleAcceleration(physicsSettingIndex, vertexIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Vertices).getValueByIndex(vertexIndex).getValueByString(Acceleration).toFloat();
	}
	/**
	* 物理点の距離の取得
	* @param physicsSettingIndex 物理演算の設定のインデックス
	* @param vertexIndex 物理点のインデックス
	* @return 物理点の距離
	*/
	getParticleRadius(physicsSettingIndex, vertexIndex) {
		return this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Vertices).getValueByIndex(vertexIndex).getValueByString(Radius).toFloat();
	}
	/**
	* 物理点の位置の取得
	* @param physicsSettingIndex 物理演算の設定のインデックス
	* @param vertexInde 物理点のインデックス
	* @return 物理点の位置
	*/
	getParticlePosition(physicsSettingIndex, vertexIndex) {
		const ret = new CubismVector2(0, 0);
		ret.x = this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Vertices).getValueByIndex(vertexIndex).getValueByString(Position).getValueByString(X).toFloat();
		ret.y = this._json.getRoot().getValueByString(PhysicsSettings).getValueByIndex(physicsSettingIndex).getValueByString(Vertices).getValueByIndex(vertexIndex).getValueByString(Position).getValueByString(Y).toFloat();
		return ret;
	}
};
var Live2DCubismFramework$9;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismPhysicsJson = CubismPhysicsJson;
})(Live2DCubismFramework$9 || (Live2DCubismFramework$9 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/physics/cubismphysics.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
var PhysicsTypeTagX = "X";
var PhysicsTypeTagY = "Y";
var PhysicsTypeTagAngle = "Angle";
var AirResistance = 5;
var MaximumWeight = 100;
var MovementThreshold = .001;
var MaxDeltaTime = 5;
/**
* 物理演算クラス
*/
var CubismPhysics = class CubismPhysics {
	/**
	* インスタンスの作成
	* @param buffer    physics3.jsonが読み込まれているバッファ
	* @param size      バッファのサイズ
	* @return 作成されたインスタンス
	*/
	static create(buffer, size) {
		const ret = new CubismPhysics();
		ret.parse(buffer, size);
		ret._physicsRig.gravity.y = 0;
		return ret;
	}
	/**
	* インスタンスを破棄する
	* @param physics 破棄するインスタンス
	*/
	static delete(physics) {
		if (physics != null) {
			physics.release();
			physics = null;
		}
	}
	/**
	* physics3.jsonをパースする。
	* @param physicsJson physics3.jsonが読み込まれているバッファ
	* @param size バッファのサイズ
	*/
	parse(physicsJson, size) {
		this._physicsRig = new CubismPhysicsRig();
		let json = new CubismPhysicsJson(physicsJson, size);
		this._physicsRig.gravity = json.getGravity();
		this._physicsRig.wind = json.getWind();
		this._physicsRig.subRigCount = json.getSubRigCount();
		this._physicsRig.fps = json.getFps();
		updateSize(this._physicsRig.settings, this._physicsRig.subRigCount, CubismPhysicsSubRig, true);
		updateSize(this._physicsRig.inputs, json.getTotalInputCount(), CubismPhysicsInput, true);
		updateSize(this._physicsRig.outputs, json.getTotalOutputCount(), CubismPhysicsOutput, true);
		updateSize(this._physicsRig.particles, json.getVertexCount(), CubismPhysicsParticle, true);
		this._currentRigOutputs.length = 0;
		this._previousRigOutputs.length = 0;
		let inputIndex = 0, outputIndex = 0, particleIndex = 0;
		let dstIndexCurrentRigOutputs = this._currentRigOutputs.length;
		let dstIndexPreviousRigOutputs = this._previousRigOutputs.length;
		this._currentRigOutputs.length += this._physicsRig.settings.length;
		this._previousRigOutputs.length += this._physicsRig.settings.length;
		for (let i = 0; i < this._physicsRig.settings.length; ++i) {
			this._physicsRig.settings[i].normalizationPosition.minimum = json.getNormalizationPositionMinimumValue(i);
			this._physicsRig.settings[i].normalizationPosition.maximum = json.getNormalizationPositionMaximumValue(i);
			this._physicsRig.settings[i].normalizationPosition.defalut = json.getNormalizationPositionDefaultValue(i);
			this._physicsRig.settings[i].normalizationAngle.minimum = json.getNormalizationAngleMinimumValue(i);
			this._physicsRig.settings[i].normalizationAngle.maximum = json.getNormalizationAngleMaximumValue(i);
			this._physicsRig.settings[i].normalizationAngle.defalut = json.getNormalizationAngleDefaultValue(i);
			this._physicsRig.settings[i].inputCount = json.getInputCount(i);
			this._physicsRig.settings[i].baseInputIndex = inputIndex;
			for (let j = 0; j < this._physicsRig.settings[i].inputCount; ++j) {
				this._physicsRig.inputs[inputIndex + j].sourceParameterIndex = -1;
				this._physicsRig.inputs[inputIndex + j].weight = json.getInputWeight(i, j);
				this._physicsRig.inputs[inputIndex + j].reflect = json.getInputReflect(i, j);
				if (json.getInputType(i, j) == PhysicsTypeTagX) {
					this._physicsRig.inputs[inputIndex + j].type = CubismPhysicsSource.CubismPhysicsSource_X;
					this._physicsRig.inputs[inputIndex + j].getNormalizedParameterValue = getInputTranslationXFromNormalizedParameterValue;
				} else if (json.getInputType(i, j) == PhysicsTypeTagY) {
					this._physicsRig.inputs[inputIndex + j].type = CubismPhysicsSource.CubismPhysicsSource_Y;
					this._physicsRig.inputs[inputIndex + j].getNormalizedParameterValue = getInputTranslationYFromNormalizedParamterValue;
				} else if (json.getInputType(i, j) == PhysicsTypeTagAngle) {
					this._physicsRig.inputs[inputIndex + j].type = CubismPhysicsSource.CubismPhysicsSource_Angle;
					this._physicsRig.inputs[inputIndex + j].getNormalizedParameterValue = getInputAngleFromNormalizedParameterValue;
				}
				this._physicsRig.inputs[inputIndex + j].source.targetType = CubismPhysicsTargetType.CubismPhysicsTargetType_Parameter;
				this._physicsRig.inputs[inputIndex + j].source.id = json.getInputSourceId(i, j);
			}
			inputIndex += this._physicsRig.settings[i].inputCount;
			this._physicsRig.settings[i].outputCount = json.getOutputCount(i);
			this._physicsRig.settings[i].baseOutputIndex = outputIndex;
			const currentRigOutput = new PhysicsOutput();
			updateSize(currentRigOutput.outputs, this._physicsRig.settings[i].outputCount, null, true);
			const previousRigOutput = new PhysicsOutput();
			updateSize(previousRigOutput.outputs, this._physicsRig.settings[i].outputCount, null, true);
			for (let j = 0; j < this._physicsRig.settings[i].outputCount; ++j) {
				currentRigOutput.outputs[j] = 0;
				previousRigOutput.outputs[j] = 0;
				this._physicsRig.outputs[outputIndex + j].destinationParameterIndex = -1;
				this._physicsRig.outputs[outputIndex + j].vertexIndex = json.getOutputVertexIndex(i, j);
				this._physicsRig.outputs[outputIndex + j].angleScale = json.getOutputAngleScale(i, j);
				this._physicsRig.outputs[outputIndex + j].weight = json.getOutputWeight(i, j);
				this._physicsRig.outputs[outputIndex + j].destination.targetType = CubismPhysicsTargetType.CubismPhysicsTargetType_Parameter;
				this._physicsRig.outputs[outputIndex + j].destination.id = json.getOutputDestinationId(i, j);
				if (json.getOutputType(i, j) == PhysicsTypeTagX) {
					this._physicsRig.outputs[outputIndex + j].type = CubismPhysicsSource.CubismPhysicsSource_X;
					this._physicsRig.outputs[outputIndex + j].getValue = getOutputTranslationX;
					this._physicsRig.outputs[outputIndex + j].getScale = getOutputScaleTranslationX;
				} else if (json.getOutputType(i, j) == PhysicsTypeTagY) {
					this._physicsRig.outputs[outputIndex + j].type = CubismPhysicsSource.CubismPhysicsSource_Y;
					this._physicsRig.outputs[outputIndex + j].getValue = getOutputTranslationY;
					this._physicsRig.outputs[outputIndex + j].getScale = getOutputScaleTranslationY;
				} else if (json.getOutputType(i, j) == PhysicsTypeTagAngle) {
					this._physicsRig.outputs[outputIndex + j].type = CubismPhysicsSource.CubismPhysicsSource_Angle;
					this._physicsRig.outputs[outputIndex + j].getValue = getOutputAngle;
					this._physicsRig.outputs[outputIndex + j].getScale = getOutputScaleAngle;
				}
				this._physicsRig.outputs[outputIndex + j].reflect = json.getOutputReflect(i, j);
			}
			this._currentRigOutputs[dstIndexCurrentRigOutputs++] = currentRigOutput;
			this._previousRigOutputs[dstIndexPreviousRigOutputs++] = previousRigOutput;
			outputIndex += this._physicsRig.settings[i].outputCount;
			this._physicsRig.settings[i].particleCount = json.getParticleCount(i);
			this._physicsRig.settings[i].baseParticleIndex = particleIndex;
			for (let j = 0; j < this._physicsRig.settings[i].particleCount; ++j) {
				this._physicsRig.particles[particleIndex + j].mobility = json.getParticleMobility(i, j);
				this._physicsRig.particles[particleIndex + j].delay = json.getParticleDelay(i, j);
				this._physicsRig.particles[particleIndex + j].acceleration = json.getParticleAcceleration(i, j);
				this._physicsRig.particles[particleIndex + j].radius = json.getParticleRadius(i, j);
				this._physicsRig.particles[particleIndex + j].position = json.getParticlePosition(i, j);
			}
			particleIndex += this._physicsRig.settings[i].particleCount;
		}
		this.initialize();
		json.release();
		json = void 0;
		json = null;
	}
	/**
	* 現在のパラメータ値で物理演算が安定化する状態を演算する。
	* @param model 物理演算の結果を適用するモデル
	*/
	stabilization(model) {
		let totalAngle;
		let weight;
		let radAngle;
		let outputValue;
		const totalTranslation = new CubismVector2();
		let currentSetting;
		let currentInputs;
		let currentOutputs;
		let currentParticles;
		const parameterValues = model.getModel().parameters.values;
		const parameterMaximumValues = model.getModel().parameters.maximumValues;
		const parameterMinimumValues = model.getModel().parameters.minimumValues;
		const parameterDefaultValues = model.getModel().parameters.defaultValues;
		if ((this._parameterCaches?.length ?? 0) < model.getParameterCount()) this._parameterCaches = new Float32Array(model.getParameterCount());
		if ((this._parameterInputCaches?.length ?? 0) < model.getParameterCount()) this._parameterInputCaches = new Float32Array(model.getParameterCount());
		for (let j = 0; j < model.getParameterCount(); ++j) {
			this._parameterCaches[j] = parameterValues[j];
			this._parameterInputCaches[j] = parameterValues[j];
		}
		for (let settingIndex = 0; settingIndex < this._physicsRig.subRigCount; ++settingIndex) {
			totalAngle = { angle: 0 };
			totalTranslation.x = 0;
			totalTranslation.y = 0;
			currentSetting = this._physicsRig.settings[settingIndex];
			currentInputs = this._physicsRig.inputs.slice(currentSetting.baseInputIndex);
			currentOutputs = this._physicsRig.outputs.slice(currentSetting.baseOutputIndex);
			currentParticles = this._physicsRig.particles.slice(currentSetting.baseParticleIndex);
			for (let i = 0; i < currentSetting.inputCount; ++i) {
				weight = currentInputs[i].weight / MaximumWeight;
				if (currentInputs[i].sourceParameterIndex == -1) currentInputs[i].sourceParameterIndex = model.getParameterIndex(currentInputs[i].source.id);
				currentInputs[i].getNormalizedParameterValue(totalTranslation, totalAngle, parameterValues[currentInputs[i].sourceParameterIndex], parameterMinimumValues[currentInputs[i].sourceParameterIndex], parameterMaximumValues[currentInputs[i].sourceParameterIndex], parameterDefaultValues[currentInputs[i].sourceParameterIndex], currentSetting.normalizationPosition, currentSetting.normalizationAngle, currentInputs[i].reflect, weight);
				this._parameterCaches[currentInputs[i].sourceParameterIndex] = parameterValues[currentInputs[i].sourceParameterIndex];
			}
			radAngle = CubismMath.degreesToRadian(-totalAngle.angle);
			totalTranslation.x = totalTranslation.x * CubismMath.cos(radAngle) - totalTranslation.y * CubismMath.sin(radAngle);
			totalTranslation.y = totalTranslation.x * CubismMath.sin(radAngle) + totalTranslation.y * CubismMath.cos(radAngle);
			updateParticlesForStabilization(currentParticles, currentSetting.particleCount, totalTranslation, totalAngle.angle, this._options.wind, MovementThreshold * currentSetting.normalizationPosition.maximum);
			for (let i = 0; i < currentSetting.outputCount; ++i) {
				const particleIndex = currentOutputs[i].vertexIndex;
				if (currentOutputs[i].destinationParameterIndex == -1) currentOutputs[i].destinationParameterIndex = model.getParameterIndex(currentOutputs[i].destination.id);
				if (particleIndex < 1 || particleIndex >= currentSetting.particleCount) continue;
				let translation = new CubismVector2();
				translation = currentParticles[particleIndex].position.substract(currentParticles[particleIndex - 1].position);
				outputValue = currentOutputs[i].getValue(translation, currentParticles, particleIndex, currentOutputs[i].reflect, this._options.gravity);
				this._currentRigOutputs[settingIndex].outputs[i] = outputValue;
				this._previousRigOutputs[settingIndex].outputs[i] = outputValue;
				const destinationParameterIndex = currentOutputs[i].destinationParameterIndex;
				const outParameterCaches = !Float32Array.prototype.slice && "subarray" in Float32Array.prototype ? JSON.parse(JSON.stringify(parameterValues.subarray(destinationParameterIndex))) : parameterValues.slice(destinationParameterIndex);
				updateOutputParameterValue(outParameterCaches, parameterMinimumValues[destinationParameterIndex], parameterMaximumValues[destinationParameterIndex], outputValue, currentOutputs[i]);
				for (let offset = destinationParameterIndex, outParamIndex = 0; offset < this._parameterCaches.length; offset++, outParamIndex++) parameterValues[offset] = this._parameterCaches[offset] = outParameterCaches[outParamIndex];
			}
		}
	}
	/**
	* 物理演算の評価
	*
	* Pendulum interpolation weights
	*
	* 振り子の計算結果は保存され、パラメータへの出力は保存された前回の結果で補間されます。
	* The result of the pendulum calculation is saved and
	* the output to the parameters is interpolated with the saved previous result of the pendulum calculation.
	*
	* 図で示すと[1]と[2]で補間されます。
	* The figure shows the interpolation between [1] and [2].
	*
	* 補間の重みは最新の振り子計算タイミングと次回のタイミングの間で見た現在時間で決定する。
	* The weight of the interpolation are determined by the current time seen between
	* the latest pendulum calculation timing and the next timing.
	*
	* 図で示すと[2]と[4]の間でみた(3)の位置の重みになる。
	* Figure shows the weight of position (3) as seen between [2] and [4].
	*
	* 解釈として振り子計算のタイミングと重み計算のタイミングがズレる。
	* As an interpretation, the pendulum calculation and weights are misaligned.
	*
	* physics3.jsonにFPS情報が存在しない場合は常に前の振り子状態で設定される。
	* If there is no FPS information in physics3.json, it is always set in the previous pendulum state.
	*
	* この仕様は補間範囲を逸脱したことが原因の震えたような見た目を回避を目的にしている。
	* The purpose of this specification is to avoid the quivering appearance caused by deviations from the interpolation range.
	*
	* ------------ time -------------->
	*
	*                 |+++++|------| <- weight
	* ==[1]====#=====[2]---(3)----(4)
	*          ^ output contents
	*
	* 1:_previousRigOutputs
	* 2:_currentRigOutputs
	* 3:_currentRemainTime (now rendering)
	* 4:next particles timing
	* @param model 物理演算の結果を適用するモデル
	* @param deltaTimeSeconds デルタ時間[秒]
	*/
	evaluate(model, deltaTimeSeconds) {
		let totalAngle;
		let weight;
		let radAngle;
		let outputValue;
		const totalTranslation = new CubismVector2();
		let currentSetting;
		let currentInputs;
		let currentOutputs;
		let currentParticles;
		if (0 >= deltaTimeSeconds) return;
		const parameterValues = model.getModel().parameters.values;
		const parameterMaximumValues = model.getModel().parameters.maximumValues;
		const parameterMinimumValues = model.getModel().parameters.minimumValues;
		const parameterDefaultValues = model.getModel().parameters.defaultValues;
		let physicsDeltaTime;
		this._currentRemainTime += deltaTimeSeconds;
		if (this._currentRemainTime > MaxDeltaTime) this._currentRemainTime = 0;
		if ((this._parameterCaches?.length ?? 0) < model.getParameterCount()) this._parameterCaches = new Float32Array(model.getParameterCount());
		if ((this._parameterInputCaches?.length ?? 0) < model.getParameterCount()) {
			this._parameterInputCaches = new Float32Array(model.getParameterCount());
			for (let j = 0; j < model.getParameterCount(); ++j) this._parameterInputCaches[j] = parameterValues[j];
		}
		if (this._physicsRig.fps > 0) physicsDeltaTime = 1 / this._physicsRig.fps;
		else physicsDeltaTime = deltaTimeSeconds;
		while (this._currentRemainTime >= physicsDeltaTime) {
			for (let settingIndex = 0; settingIndex < this._physicsRig.subRigCount; ++settingIndex) {
				currentSetting = this._physicsRig.settings[settingIndex];
				currentOutputs = this._physicsRig.outputs.slice(currentSetting.baseOutputIndex);
				for (let i = 0; i < currentSetting.outputCount; ++i) this._previousRigOutputs[settingIndex].outputs[i] = this._currentRigOutputs[settingIndex].outputs[i];
			}
			const inputWeight = physicsDeltaTime / this._currentRemainTime;
			for (let j = 0; j < model.getParameterCount(); ++j) {
				this._parameterCaches[j] = this._parameterInputCaches[j] * (1 - inputWeight) + parameterValues[j] * inputWeight;
				this._parameterInputCaches[j] = this._parameterCaches[j];
			}
			for (let settingIndex = 0; settingIndex < this._physicsRig.subRigCount; ++settingIndex) {
				totalAngle = { angle: 0 };
				totalTranslation.x = 0;
				totalTranslation.y = 0;
				currentSetting = this._physicsRig.settings[settingIndex];
				currentInputs = this._physicsRig.inputs.slice(currentSetting.baseInputIndex);
				currentOutputs = this._physicsRig.outputs.slice(currentSetting.baseOutputIndex);
				currentParticles = this._physicsRig.particles.slice(currentSetting.baseParticleIndex);
				for (let i = 0; i < currentSetting.inputCount; ++i) {
					weight = currentInputs[i].weight / MaximumWeight;
					if (currentInputs[i].sourceParameterIndex == -1) currentInputs[i].sourceParameterIndex = model.getParameterIndex(currentInputs[i].source.id);
					currentInputs[i].getNormalizedParameterValue(totalTranslation, totalAngle, this._parameterCaches[currentInputs[i].sourceParameterIndex], parameterMinimumValues[currentInputs[i].sourceParameterIndex], parameterMaximumValues[currentInputs[i].sourceParameterIndex], parameterDefaultValues[currentInputs[i].sourceParameterIndex], currentSetting.normalizationPosition, currentSetting.normalizationAngle, currentInputs[i].reflect, weight);
				}
				radAngle = CubismMath.degreesToRadian(-totalAngle.angle);
				totalTranslation.x = totalTranslation.x * CubismMath.cos(radAngle) - totalTranslation.y * CubismMath.sin(radAngle);
				totalTranslation.y = totalTranslation.x * CubismMath.sin(radAngle) + totalTranslation.y * CubismMath.cos(radAngle);
				updateParticles(currentParticles, currentSetting.particleCount, totalTranslation, totalAngle.angle, this._options.wind, MovementThreshold * currentSetting.normalizationPosition.maximum, physicsDeltaTime, AirResistance);
				for (let i = 0; i < currentSetting.outputCount; ++i) {
					const particleIndex = currentOutputs[i].vertexIndex;
					if (currentOutputs[i].destinationParameterIndex == -1) currentOutputs[i].destinationParameterIndex = model.getParameterIndex(currentOutputs[i].destination.id);
					if (particleIndex < 1 || particleIndex >= currentSetting.particleCount) continue;
					const translation = new CubismVector2();
					translation.x = currentParticles[particleIndex].position.x - currentParticles[particleIndex - 1].position.x;
					translation.y = currentParticles[particleIndex].position.y - currentParticles[particleIndex - 1].position.y;
					outputValue = currentOutputs[i].getValue(translation, currentParticles, particleIndex, currentOutputs[i].reflect, this._options.gravity);
					this._currentRigOutputs[settingIndex].outputs[i] = outputValue;
					const destinationParameterIndex = currentOutputs[i].destinationParameterIndex;
					const outParameterCaches = !Float32Array.prototype.slice && "subarray" in Float32Array.prototype ? JSON.parse(JSON.stringify(this._parameterCaches.subarray(destinationParameterIndex))) : this._parameterCaches.slice(destinationParameterIndex);
					updateOutputParameterValue(outParameterCaches, parameterMinimumValues[destinationParameterIndex], parameterMaximumValues[destinationParameterIndex], outputValue, currentOutputs[i]);
					for (let offset = destinationParameterIndex, outParamIndex = 0; offset < this._parameterCaches.length; offset++, outParamIndex++) this._parameterCaches[offset] = outParameterCaches[outParamIndex];
				}
			}
			this._currentRemainTime -= physicsDeltaTime;
		}
		const alpha = this._currentRemainTime / physicsDeltaTime;
		this.interpolate(model, alpha);
	}
	/**
	* 物理演算結果の適用
	* 振り子演算の最新の結果と一つ前の結果から指定した重みで適用する。
	* @param model 物理演算の結果を適用するモデル
	* @param weight 最新結果の重み
	*/
	interpolate(model, weight) {
		let currentOutputs;
		let currentSetting;
		const parameterValues = model.getModel().parameters.values;
		const parameterMaximumValues = model.getModel().parameters.maximumValues;
		const parameterMinimumValues = model.getModel().parameters.minimumValues;
		for (let settingIndex = 0; settingIndex < this._physicsRig.subRigCount; ++settingIndex) {
			currentSetting = this._physicsRig.settings[settingIndex];
			currentOutputs = this._physicsRig.outputs.slice(currentSetting.baseOutputIndex);
			for (let i = 0; i < currentSetting.outputCount; ++i) {
				if (currentOutputs[i].destinationParameterIndex == -1) continue;
				const destinationParameterIndex = currentOutputs[i].destinationParameterIndex;
				const outParameterValues = !Float32Array.prototype.slice && "subarray" in Float32Array.prototype ? JSON.parse(JSON.stringify(parameterValues.subarray(destinationParameterIndex))) : parameterValues.slice(destinationParameterIndex);
				updateOutputParameterValue(outParameterValues, parameterMinimumValues[destinationParameterIndex], parameterMaximumValues[destinationParameterIndex], this._previousRigOutputs[settingIndex].outputs[i] * (1 - weight) + this._currentRigOutputs[settingIndex].outputs[i] * weight, currentOutputs[i]);
				for (let offset = destinationParameterIndex, outParamIndex = 0; offset < parameterValues.length; offset++, outParamIndex++) parameterValues[offset] = outParameterValues[outParamIndex];
			}
		}
	}
	/**
	* オプションの設定
	* @param options オプション
	*/
	setOptions(options) {
		this._options = options;
	}
	/**
	* オプションの取得
	* @return オプション
	*/
	getOption() {
		return this._options;
	}
	/**
	* コンストラクタ
	*/
	constructor() {
		this._physicsRig = null;
		this._options = new Options();
		this._options.gravity.y = -1;
		this._options.gravity.x = 0;
		this._options.wind.x = 0;
		this._options.wind.y = 0;
		this._currentRigOutputs = new Array();
		this._previousRigOutputs = new Array();
		this._currentRemainTime = 0;
		this._parameterCaches = null;
		this._parameterInputCaches = null;
	}
	/**
	* デストラクタ相当の処理
	*/
	release() {
		this._physicsRig = void 0;
		this._physicsRig = null;
	}
	/**
	* 初期化する
	*/
	initialize() {
		let strand;
		let currentSetting;
		let radius;
		for (let settingIndex = 0; settingIndex < this._physicsRig.subRigCount; ++settingIndex) {
			currentSetting = this._physicsRig.settings[settingIndex];
			strand = this._physicsRig.particles.slice(currentSetting.baseParticleIndex);
			strand[0].initialPosition = new CubismVector2(0, 0);
			strand[0].lastPosition = new CubismVector2(strand[0].initialPosition.x, strand[0].initialPosition.y);
			strand[0].lastGravity = new CubismVector2(0, -1);
			strand[0].lastGravity.y *= -1;
			strand[0].velocity = new CubismVector2(0, 0);
			strand[0].force = new CubismVector2(0, 0);
			for (let i = 1; i < currentSetting.particleCount; ++i) {
				radius = new CubismVector2(0, 0);
				radius.y = strand[i].radius;
				strand[i].initialPosition = new CubismVector2(strand[i - 1].initialPosition.x + radius.x, strand[i - 1].initialPosition.y + radius.y);
				strand[i].position = new CubismVector2(strand[i].initialPosition.x, strand[i].initialPosition.y);
				strand[i].lastPosition = new CubismVector2(strand[i].initialPosition.x, strand[i].initialPosition.y);
				strand[i].lastGravity = new CubismVector2(0, -1);
				strand[i].lastGravity.y *= -1;
				strand[i].velocity = new CubismVector2(0, 0);
				strand[i].force = new CubismVector2(0, 0);
			}
		}
	}
};
/**
* 物理演算のオプション
*/
var Options = class {
	constructor() {
		this.gravity = new CubismVector2(0, 0);
		this.wind = new CubismVector2(0, 0);
	}
};
/**
* パラメータに適用する前の物理演算の出力結果
*/
var PhysicsOutput = class {
	constructor() {
		this.outputs = new Array(0);
	}
};
/**
* Gets sign.
*
* @param value Evaluation target value.
*
* @return Sign of value.
*/
function sign(value) {
	let ret = 0;
	if (value > 0) ret = 1;
	else if (value < 0) ret = -1;
	return ret;
}
function getInputTranslationXFromNormalizedParameterValue(targetTranslation, targetAngle, value, parameterMinimumValue, parameterMaximumValue, parameterDefaultValue, normalizationPosition, normalizationAngle, isInverted, weight) {
	targetTranslation.x += normalizeParameterValue(value, parameterMinimumValue, parameterMaximumValue, parameterDefaultValue, normalizationPosition.minimum, normalizationPosition.maximum, normalizationPosition.defalut, isInverted) * weight;
}
function getInputTranslationYFromNormalizedParamterValue(targetTranslation, targetAngle, value, parameterMinimumValue, parameterMaximumValue, parameterDefaultValue, normalizationPosition, normalizationAngle, isInverted, weight) {
	targetTranslation.y += normalizeParameterValue(value, parameterMinimumValue, parameterMaximumValue, parameterDefaultValue, normalizationPosition.minimum, normalizationPosition.maximum, normalizationPosition.defalut, isInverted) * weight;
}
function getInputAngleFromNormalizedParameterValue(targetTranslation, targetAngle, value, parameterMinimumValue, parameterMaximumValue, parameterDefaultValue, normalizaitionPosition, normalizationAngle, isInverted, weight) {
	targetAngle.angle += normalizeParameterValue(value, parameterMinimumValue, parameterMaximumValue, parameterDefaultValue, normalizationAngle.minimum, normalizationAngle.maximum, normalizationAngle.defalut, isInverted) * weight;
}
function getOutputTranslationX(translation, particles, particleIndex, isInverted, parentGravity) {
	let outputValue = translation.x;
	if (isInverted) outputValue *= -1;
	return outputValue;
}
function getOutputTranslationY(translation, particles, particleIndex, isInverted, parentGravity) {
	let outputValue = translation.y;
	if (isInverted) outputValue *= -1;
	return outputValue;
}
function getOutputAngle(translation, particles, particleIndex, isInverted, parentGravity) {
	let outputValue;
	if (particleIndex >= 2) parentGravity = particles[particleIndex - 1].position.substract(particles[particleIndex - 2].position);
	else parentGravity = parentGravity.multiplyByScaler(-1);
	outputValue = CubismMath.directionToRadian(parentGravity, translation);
	if (isInverted) outputValue *= -1;
	return outputValue;
}
function getRangeValue(min, max) {
	const maxValue = CubismMath.max(min, max);
	const minValue = CubismMath.min(min, max);
	return CubismMath.abs(maxValue - minValue);
}
function getDefaultValue(min, max) {
	return CubismMath.min(min, max) + getRangeValue(min, max) / 2;
}
function getOutputScaleTranslationX(translationScale, angleScale) {
	return JSON.parse(JSON.stringify(translationScale.x));
}
function getOutputScaleTranslationY(translationScale, angleScale) {
	return JSON.parse(JSON.stringify(translationScale.y));
}
function getOutputScaleAngle(translationScale, angleScale) {
	return JSON.parse(JSON.stringify(angleScale));
}
/**
* Updates particles.
*
* @param strand                Target array of particle.
* @param strandCount           Count of particle.
* @param totalTranslation      Total translation value.
* @param totalAngle            Total angle.
* @param windDirection         Direction of Wind.
* @param thresholdValue        Threshold of movement.
* @param deltaTimeSeconds      Delta time.
* @param airResistance         Air resistance.
*/
function updateParticles(strand, strandCount, totalTranslation, totalAngle, windDirection, thresholdValue, deltaTimeSeconds, airResistance) {
	let delay;
	let radian;
	let direction = new CubismVector2(0, 0);
	let velocity = new CubismVector2(0, 0);
	let force = new CubismVector2(0, 0);
	let newDirection = new CubismVector2(0, 0);
	strand[0].position = new CubismVector2(totalTranslation.x, totalTranslation.y);
	const totalRadian = CubismMath.degreesToRadian(totalAngle);
	const currentGravity = CubismMath.radianToDirection(totalRadian);
	currentGravity.normalize();
	for (let i = 1; i < strandCount; ++i) {
		strand[i].force = currentGravity.multiplyByScaler(strand[i].acceleration).add(windDirection);
		strand[i].lastPosition = new CubismVector2(strand[i].position.x, strand[i].position.y);
		delay = strand[i].delay * deltaTimeSeconds * 30;
		direction = strand[i].position.substract(strand[i - 1].position);
		radian = CubismMath.directionToRadian(strand[i].lastGravity, currentGravity) / airResistance;
		direction.x = CubismMath.cos(radian) * direction.x - direction.y * CubismMath.sin(radian);
		direction.y = CubismMath.sin(radian) * direction.x + direction.y * CubismMath.cos(radian);
		strand[i].position = strand[i - 1].position.add(direction);
		velocity = strand[i].velocity.multiplyByScaler(delay);
		force = strand[i].force.multiplyByScaler(delay).multiplyByScaler(delay);
		strand[i].position = strand[i].position.add(velocity).add(force);
		newDirection = strand[i].position.substract(strand[i - 1].position);
		newDirection.normalize();
		strand[i].position = strand[i - 1].position.add(newDirection.multiplyByScaler(strand[i].radius));
		if (CubismMath.abs(strand[i].position.x) < thresholdValue) strand[i].position.x = 0;
		if (delay != 0) {
			strand[i].velocity = strand[i].position.substract(strand[i].lastPosition);
			strand[i].velocity = strand[i].velocity.divisionByScalar(delay);
			strand[i].velocity = strand[i].velocity.multiplyByScaler(strand[i].mobility);
		}
		strand[i].force = new CubismVector2(0, 0);
		strand[i].lastGravity = new CubismVector2(currentGravity.x, currentGravity.y);
	}
}
/**
* Updates particles for stabilization.
*
* @param strand                Target array of particle.
* @param strandCount           Count of particle.
* @param totalTranslation      Total translation value.
* @param totalAngle            Total angle.
* @param windDirection         Direction of Wind.
* @param thresholdValue        Threshold of movement.
*/
function updateParticlesForStabilization(strand, strandCount, totalTranslation, totalAngle, windDirection, thresholdValue) {
	let force = new CubismVector2(0, 0);
	strand[0].position = new CubismVector2(totalTranslation.x, totalTranslation.y);
	const totalRadian = CubismMath.degreesToRadian(totalAngle);
	const currentGravity = CubismMath.radianToDirection(totalRadian);
	currentGravity.normalize();
	for (let i = 1; i < strandCount; ++i) {
		strand[i].force = currentGravity.multiplyByScaler(strand[i].acceleration).add(windDirection);
		strand[i].lastPosition = new CubismVector2(strand[i].position.x, strand[i].position.y);
		strand[i].velocity = new CubismVector2(0, 0);
		force = strand[i].force;
		force.normalize();
		force = force.multiplyByScaler(strand[i].radius);
		strand[i].position = strand[i - 1].position.add(force);
		if (CubismMath.abs(strand[i].position.x) < thresholdValue) strand[i].position.x = 0;
		strand[i].force = new CubismVector2(0, 0);
		strand[i].lastGravity = new CubismVector2(currentGravity.x, currentGravity.y);
	}
}
/**
* Updates output parameter value.
* @param parameterValue            Target parameter value.
* @param parameterValueMinimum     Minimum of parameter value.
* @param parameterValueMaximum     Maximum of parameter value.
* @param translation               Translation value.
*/
function updateOutputParameterValue(parameterValue, parameterValueMinimum, parameterValueMaximum, translation, output) {
	let value;
	value = translation * output.getScale(output.translationScale, output.angleScale);
	if (value < parameterValueMinimum) {
		if (value < output.valueBelowMinimum) output.valueBelowMinimum = value;
		value = parameterValueMinimum;
	} else if (value > parameterValueMaximum) {
		if (value > output.valueExceededMaximum) output.valueExceededMaximum = value;
		value = parameterValueMaximum;
	}
	const weight = output.weight / MaximumWeight;
	if (weight >= 1) parameterValue[0] = value;
	else {
		value = parameterValue[0] * (1 - weight) + value * weight;
		parameterValue[0] = value;
	}
}
function normalizeParameterValue(value, parameterMinimum, parameterMaximum, parameterDefault, normalizedMinimum, normalizedMaximum, normalizedDefault, isInverted) {
	let result = 0;
	const maxValue = CubismMath.max(parameterMaximum, parameterMinimum);
	if (maxValue < value) value = maxValue;
	const minValue = CubismMath.min(parameterMaximum, parameterMinimum);
	if (minValue > value) value = minValue;
	const minNormValue = CubismMath.min(normalizedMinimum, normalizedMaximum);
	const maxNormValue = CubismMath.max(normalizedMinimum, normalizedMaximum);
	const middleNormValue = normalizedDefault;
	const middleValue = getDefaultValue(minValue, maxValue);
	const paramValue = value - middleValue;
	switch (sign(paramValue)) {
		case 1: {
			const nLength = maxNormValue - middleNormValue;
			const pLength = maxValue - middleValue;
			if (pLength != 0) {
				result = paramValue * (nLength / pLength);
				result += middleNormValue;
			}
			break;
		}
		case -1: {
			const nLength = minNormValue - middleNormValue;
			const pLength = minValue - middleValue;
			if (pLength != 0) {
				result = paramValue * (nLength / pLength);
				result += middleNormValue;
			}
			break;
		}
		case 0: result = middleNormValue;
	}
	return isInverted ? result : result * -1;
}
var Live2DCubismFramework$8;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismPhysics = CubismPhysics;
	_Live2DCubismFramework.Options = Options;
})(Live2DCubismFramework$8 || (Live2DCubismFramework$8 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/model/cubismmodelmultiplyandscreencolor.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
/**
* SDK側から与えられた描画オブジェクトの乗算色・スクリーン色上書きフラグと
* その色を保持する構造体
*/
var ColorData = class {
	constructor(isOverridden = false, color = new CubismTextureColor()) {
		this.isOverridden = isOverridden;
		this.color = color;
	}
};
/**
* Handling multiply and screen colors of the model.
*/
var CubismModelMultiplyAndScreenColor = class {
	/**
	* Constructor.
	*
	* @param model cubism model.
	*/
	constructor(model) {
		this._model = model;
		this._isOverriddenModelMultiplyColors = false;
		this._isOverriddenModelScreenColors = false;
		this._userPartScreenColors = [];
		this._userPartMultiplyColors = [];
		this._userDrawableScreenColors = [];
		this._userDrawableMultiplyColors = [];
		this._userOffscreenScreenColors = [];
		this._userOffscreenMultiplyColors = [];
	}
	/**
	* Initialization for using multiply and screen colors.
	*
	* @param partCount number of parts.
	* @param drawableCount number of drawables.
	* @param offscreenCount number of offscreen.
	*/
	initialize(partCount, drawableCount, offscreenCount) {
		const userMultiplyColor = new ColorData(false, new CubismTextureColor(1, 1, 1, 1));
		const userScreenColor = new ColorData(false, new CubismTextureColor(0, 0, 0, 1));
		this._userPartMultiplyColors = new Array(partCount);
		this._userPartScreenColors = new Array(partCount);
		for (let i = 0; i < partCount; i++) {
			this._userPartMultiplyColors[i] = new ColorData(userMultiplyColor.isOverridden, new CubismTextureColor(userMultiplyColor.color.r, userMultiplyColor.color.g, userMultiplyColor.color.b, userMultiplyColor.color.a));
			this._userPartScreenColors[i] = new ColorData(userScreenColor.isOverridden, new CubismTextureColor(userScreenColor.color.r, userScreenColor.color.g, userScreenColor.color.b, userScreenColor.color.a));
		}
		this._userDrawableMultiplyColors = new Array(drawableCount);
		this._userDrawableScreenColors = new Array(drawableCount);
		for (let i = 0; i < drawableCount; i++) {
			this._userDrawableMultiplyColors[i] = new ColorData(userMultiplyColor.isOverridden, new CubismTextureColor(userMultiplyColor.color.r, userMultiplyColor.color.g, userMultiplyColor.color.b, userMultiplyColor.color.a));
			this._userDrawableScreenColors[i] = new ColorData(userScreenColor.isOverridden, new CubismTextureColor(userScreenColor.color.r, userScreenColor.color.g, userScreenColor.color.b, userScreenColor.color.a));
		}
		this._userOffscreenMultiplyColors = new Array(offscreenCount);
		this._userOffscreenScreenColors = new Array(offscreenCount);
		for (let i = 0; i < offscreenCount; i++) {
			this._userOffscreenMultiplyColors[i] = new ColorData(userMultiplyColor.isOverridden, new CubismTextureColor(userMultiplyColor.color.r, userMultiplyColor.color.g, userMultiplyColor.color.b, userMultiplyColor.color.a));
			this._userOffscreenScreenColors[i] = new ColorData(userScreenColor.isOverridden, new CubismTextureColor(userScreenColor.color.r, userScreenColor.color.g, userScreenColor.color.b, userScreenColor.color.a));
		}
	}
	/**
	* Outputs a warning message for index out of range errors.
	*
	* @param functionName Name of the calling function
	* @param index The invalid index value
	* @param maxIndex The maximum valid index (length - 1)
	*/
	warnIndexOutOfRange(functionName, index, maxIndex) {
		CubismLogWarning(`${functionName}: index is out of range. index=${index}, valid range=[0, ${maxIndex}].`);
	}
	/**
	* Validates if the given part index is within valid range.
	*
	* @param index Part index to validate
	* @param functionName Name of the calling function for error reporting
	* @return true if the index is valid; otherwise false
	*/
	isValidPartIndex(index, functionName) {
		if (index < 0 || index >= this._model.getPartCount()) {
			this.warnIndexOutOfRange(functionName, index, this._model.getPartCount() - 1);
			return false;
		}
		return true;
	}
	/**
	* Validates if the given drawable index is within valid range.
	*
	* @param index Drawable index to validate
	* @param functionName Name of the calling function for error reporting
	* @return true if the index is valid; otherwise false
	*/
	isValidDrawableIndex(index, functionName) {
		if (index < 0 || index >= this._model.getDrawableCount()) {
			this.warnIndexOutOfRange(functionName, index, this._model.getDrawableCount() - 1);
			return false;
		}
		return true;
	}
	/**
	* Validates if the given offscreen index is within valid range.
	*
	* @param index Offscreen index to validate
	* @param functionName Name of the calling function for error reporting
	* @return true if the index is valid; otherwise false
	*/
	isValidOffscreenIndex(index, functionName) {
		if (index < 0 || index >= this._model.getOffscreenCount()) {
			this.warnIndexOutOfRange(functionName, index, this._model.getOffscreenCount() - 1);
			return false;
		}
		return true;
	}
	/**
	* Sets the flag indicating whether the color set at runtime is used as the multiply color for the entire model during rendering.
	*
	* @param value true if the color set at runtime is to be used; otherwise false.
	*/
	setMultiplyColorEnabled(value) {
		this._isOverriddenModelMultiplyColors = value;
	}
	/**
	* Returns the flag indicating whether the color set at runtime is used as the multiply color for the entire model during rendering.
	*
	* @return true if the color set at runtime is used; otherwise false.
	*/
	getMultiplyColorEnabled() {
		return this._isOverriddenModelMultiplyColors;
	}
	/**
	* Sets the flag indicating whether the color set at runtime is used as the screen color for the entire model during rendering.
	*
	* @param value true if the color set at runtime is to be used; otherwise false.
	*/
	setScreenColorEnabled(value) {
		this._isOverriddenModelScreenColors = value;
	}
	/**
	* Returns the flag indicating whether the color set at runtime is used as the screen color for the entire model during rendering.
	*
	* @return true if the color set at runtime is used; otherwise false.
	*/
	getScreenColorEnabled() {
		return this._isOverriddenModelScreenColors;
	}
	/**
	* Sets whether the part multiply color is overridden by the SDK.
	* Use true to use the color information from the SDK, or false to use the color information from the model.
	*
	* @param partIndex Part index
	* @param value true enable override, false to disable
	*/
	setPartMultiplyColorEnabled(partIndex, value) {
		if (!this.isValidPartIndex(partIndex, "setPartMultiplyColorEnabled")) return;
		this.setPartColorEnabled(partIndex, value, this._userPartMultiplyColors, this._userDrawableMultiplyColors, this._userOffscreenMultiplyColors);
	}
	/**
	* Checks whether the part multiply color is overridden by the SDK.
	*
	* @param partIndex Part index
	*
	* @return true if the color information from the SDK is used; otherwise false.
	*/
	getPartMultiplyColorEnabled(partIndex) {
		if (!this.isValidPartIndex(partIndex, "getPartMultiplyColorEnabled")) return false;
		return this._userPartMultiplyColors[partIndex].isOverridden;
	}
	/**
	* Sets whether the part screen color is overridden by the SDK.
	* Use true to use the color information from the SDK, or false to use the color information from the model.
	*
	* @param partIndex Part index
	* @param value true enable override, false to disable
	*/
	setPartScreenColorEnabled(partIndex, value) {
		if (!this.isValidPartIndex(partIndex, "setPartScreenColorEnabled")) return;
		this.setPartColorEnabled(partIndex, value, this._userPartScreenColors, this._userDrawableScreenColors, this._userOffscreenScreenColors);
	}
	/**
	* Checks whether the part screen color is overridden by the SDK.
	*
	* @param partIndex Part index
	*
	* @return true if the color information from the SDK is used; otherwise false.
	*/
	getPartScreenColorEnabled(partIndex) {
		if (!this.isValidPartIndex(partIndex, "getPartScreenColorEnabled")) return false;
		return this._userPartScreenColors[partIndex].isOverridden;
	}
	/**
	* Sets the multiply color of the part.
	*
	* @param partIndex Part index
	* @param color Multiply color to be set (CubismTextureColor)
	*/
	setPartMultiplyColorByTextureColor(partIndex, color) {
		if (!this.isValidPartIndex(partIndex, "setPartMultiplyColorByTextureColor")) return;
		this.setPartMultiplyColorByRGBA(partIndex, color.r, color.g, color.b, color.a);
	}
	/**
	* Sets the multiply color of the part.
	*
	* @param partIndex Part index
	* @param r Red value of the multiply color to be set
	* @param g Green value of the multiply color to be set
	* @param b Blue value of the multiply color to be set
	* @param a Alpha value of the multiply color to be set
	*/
	setPartMultiplyColorByRGBA(partIndex, r, g, b, a = 1) {
		if (!this.isValidPartIndex(partIndex, "setPartMultiplyColorByRGBA")) return;
		this.setPartColor(partIndex, r, g, b, a, this._userPartMultiplyColors, this._userDrawableMultiplyColors, this._userOffscreenMultiplyColors);
	}
	/**
	* Returns the multiply color of the part.
	*
	* @param partIndex Part index
	*
	* @return Multiply color (CubismTextureColor)
	*/
	getPartMultiplyColor(partIndex) {
		if (!this.isValidPartIndex(partIndex, "getPartMultiplyColor")) return new CubismTextureColor(1, 1, 1, 1);
		return this._userPartMultiplyColors[partIndex].color;
	}
	/**
	* Sets the screen color of the part.
	*
	* @param partIndex Part index
	* @param color Screen color to be set (CubismTextureColor)
	*/
	setPartScreenColorByTextureColor(partIndex, color) {
		if (!this.isValidPartIndex(partIndex, "setPartScreenColorByTextureColor")) return;
		this.setPartScreenColorByRGBA(partIndex, color.r, color.g, color.b, color.a);
	}
	/**
	* Sets the screen color of the part.
	*
	* @param partIndex Part index
	* @param r Red value of the screen color to be set
	* @param g Green value of the screen color to be set
	* @param b Blue value of the screen color to be set
	* @param a Alpha value of the screen color to be set
	*/
	setPartScreenColorByRGBA(partIndex, r, g, b, a = 1) {
		if (!this.isValidPartIndex(partIndex, "setPartScreenColorByRGBA")) return;
		this.setPartColor(partIndex, r, g, b, a, this._userPartScreenColors, this._userDrawableScreenColors, this._userOffscreenScreenColors);
	}
	/**
	* Returns the screen color of the part.
	*
	* @param partIndex Part index
	*
	* @return Screen color (CubismTextureColor)
	*/
	getPartScreenColor(partIndex) {
		if (!this.isValidPartIndex(partIndex, "getPartScreenColor")) return new CubismTextureColor(0, 0, 0, 1);
		return this._userPartScreenColors[partIndex].color;
	}
	/**
	* Sets the flag indicating whether the color set at runtime is used as the multiply color for the drawable during rendering.
	*
	* @param drawableIndex Drawable index
	* @param value true if the color set at runtime is to be used; otherwise false.
	*/
	setDrawableMultiplyColorEnabled(drawableIndex, value) {
		if (!this.isValidDrawableIndex(drawableIndex, "setDrawableMultiplyColorEnabled")) return;
		this._userDrawableMultiplyColors[drawableIndex].isOverridden = value;
	}
	/**
	* Returns the flag indicating whether the color set at runtime is used as the multiply color for the drawable during rendering.
	*
	* @param drawableIndex Drawable index
	*
	* @return true if the color set at runtime is used; otherwise false.
	*/
	getDrawableMultiplyColorEnabled(drawableIndex) {
		if (!this.isValidDrawableIndex(drawableIndex, "getDrawableMultiplyColorEnabled")) return false;
		return this._userDrawableMultiplyColors[drawableIndex].isOverridden;
	}
	/**
	* Sets the flag indicating whether the color set at runtime is used as the screen color for the drawable during rendering.
	*
	* @param drawableIndex Drawable index
	* @param value true if the color set at runtime is to be used; otherwise false.
	*/
	setDrawableScreenColorEnabled(drawableIndex, value) {
		if (!this.isValidDrawableIndex(drawableIndex, "setDrawableScreenColorEnabled")) return;
		this._userDrawableScreenColors[drawableIndex].isOverridden = value;
	}
	/**
	* Returns the flag indicating whether the color set at runtime is used as the screen color for the drawable during rendering.
	*
	* @param drawableIndex Drawable index
	*
	* @return true if the color set at runtime is used; otherwise false.
	*/
	getDrawableScreenColorEnabled(drawableIndex) {
		if (!this.isValidDrawableIndex(drawableIndex, "getDrawableScreenColorEnabled")) return false;
		return this._userDrawableScreenColors[drawableIndex].isOverridden;
	}
	/**
	* Sets the multiply color of the drawable.
	*
	* @param drawableIndex Drawable index
	* @param color Multiply color to be set (CubismTextureColor)
	*/
	setDrawableMultiplyColorByTextureColor(drawableIndex, color) {
		if (!this.isValidDrawableIndex(drawableIndex, "setDrawableMultiplyColorByTextureColor")) return;
		this.setDrawableMultiplyColorByRGBA(drawableIndex, color.r, color.g, color.b, color.a);
	}
	/**
	* Sets the multiply color of the drawable.
	*
	* @param drawableIndex Drawable index
	* @param r Red value of the multiply color to be set
	* @param g Green value of the multiply color to be set
	* @param b Blue value of the multiply color to be set
	* @param a Alpha value of the multiply color to be set
	*/
	setDrawableMultiplyColorByRGBA(drawableIndex, r, g, b, a = 1) {
		if (!this.isValidDrawableIndex(drawableIndex, "setDrawableMultiplyColorByRGBA")) return;
		this._userDrawableMultiplyColors[drawableIndex].color.r = r;
		this._userDrawableMultiplyColors[drawableIndex].color.g = g;
		this._userDrawableMultiplyColors[drawableIndex].color.b = b;
		this._userDrawableMultiplyColors[drawableIndex].color.a = a;
	}
	/**
	* Returns the multiply color from the list of drawables.
	*
	* @param drawableIndex Drawable index
	*
	* @return Multiply color (CubismTextureColor)
	*/
	getDrawableMultiplyColor(drawableIndex) {
		if (!this.isValidDrawableIndex(drawableIndex, "getDrawableMultiplyColor")) return new CubismTextureColor(1, 1, 1, 1);
		if (this.getMultiplyColorEnabled() || this.getDrawableMultiplyColorEnabled(drawableIndex)) return this._userDrawableMultiplyColors[drawableIndex].color;
		return this._model.getDrawableMultiplyColor(drawableIndex);
	}
	/**
	* Sets the screen color of the drawable.
	*
	* @param drawableIndex Drawable index
	* @param color Screen color to be set (CubismTextureColor)
	*/
	setDrawableScreenColorByTextureColor(drawableIndex, color) {
		if (!this.isValidDrawableIndex(drawableIndex, "setDrawableScreenColorByTextureColor")) return;
		this.setDrawableScreenColorByRGBA(drawableIndex, color.r, color.g, color.b, color.a);
	}
	/**
	* Sets the screen color of the drawable.
	*
	* @param drawableIndex Drawable index
	* @param r Red value of the screen color to be set
	* @param g Green value of the screen color to be set
	* @param b Blue value of the screen color to be set
	* @param a Alpha value of the screen color to be set
	*/
	setDrawableScreenColorByRGBA(drawableIndex, r, g, b, a = 1) {
		if (!this.isValidDrawableIndex(drawableIndex, "setDrawableScreenColorByRGBA")) return;
		this._userDrawableScreenColors[drawableIndex].color.r = r;
		this._userDrawableScreenColors[drawableIndex].color.g = g;
		this._userDrawableScreenColors[drawableIndex].color.b = b;
		this._userDrawableScreenColors[drawableIndex].color.a = a;
	}
	/**
	* Returns the screen color from the list of drawables.
	*
	* @param drawableIndex Drawable index
	*
	* @return Screen color (CubismTextureColor)
	*/
	getDrawableScreenColor(drawableIndex) {
		if (!this.isValidDrawableIndex(drawableIndex, "getDrawableScreenColor")) return new CubismTextureColor(0, 0, 0, 1);
		if (this.getScreenColorEnabled() || this.getDrawableScreenColorEnabled(drawableIndex)) return this._userDrawableScreenColors[drawableIndex].color;
		return this._model.getDrawableScreenColor(drawableIndex);
	}
	/**
	* Sets whether the offscreen multiply color is overridden by the SDK.
	* Use true to use the color information from the SDK, or false to use the color information from the model.
	*
	* @param offscreenIndex Offscreen index
	* @param value true enable override, false to disable
	*/
	setOffscreenMultiplyColorEnabled(offscreenIndex, value) {
		if (!this.isValidOffscreenIndex(offscreenIndex, "setOffscreenMultiplyColorEnabled")) return;
		this._userOffscreenMultiplyColors[offscreenIndex].isOverridden = value;
	}
	/**
	* Checks whether the offscreen multiply color is overridden by the SDK.
	*
	* @param offscreenIndex Offscreen index
	*
	* @return true if the color information from the SDK is used; otherwise false.
	*/
	getOffscreenMultiplyColorEnabled(offscreenIndex) {
		if (!this.isValidOffscreenIndex(offscreenIndex, "getOffscreenMultiplyColorEnabled")) return false;
		return this._userOffscreenMultiplyColors[offscreenIndex].isOverridden;
	}
	/**
	* Sets whether the offscreen screen color is overridden by the SDK.
	* Use true to use the color information from the SDK, or false to use the color information from the model.
	*
	* @param offscreenIndex Offscreen index
	* @param value true enable override, false to disable
	*/
	setOffscreenScreenColorEnabled(offscreenIndex, value) {
		if (!this.isValidOffscreenIndex(offscreenIndex, "setOffscreenScreenColorEnabled")) return;
		this._userOffscreenScreenColors[offscreenIndex].isOverridden = value;
	}
	/**
	* Checks whether the offscreen screen color is overridden by the SDK.
	*
	* @param offscreenIndex Offscreen index
	*
	* @return true if the color information from the SDK is used; otherwise false.
	*/
	getOffscreenScreenColorEnabled(offscreenIndex) {
		if (!this.isValidOffscreenIndex(offscreenIndex, "getOffscreenScreenColorEnabled")) return false;
		return this._userOffscreenScreenColors[offscreenIndex].isOverridden;
	}
	/**
	* Sets the multiply color of the offscreen.
	*
	* @param offscreenIndex Offsscreen index
	* @param color Multiply color to be set (CubismTextureColor)
	*/
	setOffscreenMultiplyColorByTextureColor(offscreenIndex, color) {
		if (!this.isValidOffscreenIndex(offscreenIndex, "setOffscreenMultiplyColorByTextureColor")) return;
		this.setOffscreenMultiplyColorByRGBA(offscreenIndex, color.r, color.g, color.b, color.a);
	}
	/**
	* Sets the multiply color of the offscreen.
	*
	* @param offscreenIndex Offsscreen index
	* @param r Red value of the multiply color to be set
	* @param g Green value of the multiply color to be set
	* @param b Blue value of the multiply color to be set
	* @param a Alpha value of the multiply color to be set
	*/
	setOffscreenMultiplyColorByRGBA(offscreenIndex, r, g, b, a = 1) {
		if (!this.isValidOffscreenIndex(offscreenIndex, "setOffscreenMultiplyColorByRGBA")) return;
		this._userOffscreenMultiplyColors[offscreenIndex].color.r = r;
		this._userOffscreenMultiplyColors[offscreenIndex].color.g = g;
		this._userOffscreenMultiplyColors[offscreenIndex].color.b = b;
		this._userOffscreenMultiplyColors[offscreenIndex].color.a = a;
	}
	/**
	* Returns the multiply color from the list of offscreen.
	*
	* @param offscreenIndex Offsscreen index
	*
	* @return Multiply color (CubismTextureColor)
	*/
	getOffscreenMultiplyColor(offscreenIndex) {
		if (!this.isValidOffscreenIndex(offscreenIndex, "getOffscreenMultiplyColor")) return new CubismTextureColor(1, 1, 1, 1);
		if (this.getMultiplyColorEnabled() || this.getOffscreenMultiplyColorEnabled(offscreenIndex)) return this._userOffscreenMultiplyColors[offscreenIndex].color;
		return this._model.getOffscreenMultiplyColor(offscreenIndex);
	}
	/**
	* Sets the screen color of the offscreen.
	*
	* @param offscreenIndex Offsscreen index
	* @param color Screen color to be set (CubismTextureColor)
	*/
	setOffscreenScreenColorByTextureColor(offscreenIndex, color) {
		if (!this.isValidOffscreenIndex(offscreenIndex, "setOffscreenScreenColorByTextureColor")) return;
		this.setOffscreenScreenColorByRGBA(offscreenIndex, color.r, color.g, color.b, color.a);
	}
	/**
	* Sets the screen color of the offscreen.
	*
	* @param offscreenIndex Offsscreen index
	* @param r Red value of the screen color to be set
	* @param g Green value of the screen color to be set
	* @param b Blue value of the screen color to be set
	* @param a Alpha value of the screen color to be set
	*/
	setOffscreenScreenColorByRGBA(offscreenIndex, r, g, b, a = 1) {
		if (!this.isValidOffscreenIndex(offscreenIndex, "setOffscreenScreenColorByRGBA")) return;
		this._userOffscreenScreenColors[offscreenIndex].color.r = r;
		this._userOffscreenScreenColors[offscreenIndex].color.g = g;
		this._userOffscreenScreenColors[offscreenIndex].color.b = b;
		this._userOffscreenScreenColors[offscreenIndex].color.a = a;
	}
	/**
	* Returns the screen color from the list of offscreen.
	*
	* @param offscreenIndex Offsscreen index
	*
	* @return Screen color (CubismTextureColor)
	*/
	getOffscreenScreenColor(offscreenIndex) {
		if (!this.isValidOffscreenIndex(offscreenIndex, "getOffscreenScreenColor")) return new CubismTextureColor(0, 0, 0, 1);
		if (this.getScreenColorEnabled() || this.getOffscreenScreenColorEnabled(offscreenIndex)) return this._userOffscreenScreenColors[offscreenIndex].color;
		return this._model.getOffscreenScreenColor(offscreenIndex);
	}
	/**
	* Sets the part color with hierarchical propagation (internal method)
	*/
	setPartColor(partIndex, r, g, b, a, partColors, drawableColors, offscreenColors) {
		partColors[partIndex].color.r = r;
		partColors[partIndex].color.g = g;
		partColors[partIndex].color.b = b;
		partColors[partIndex].color.a = a;
		if (partColors[partIndex].isOverridden) {
			const offscreenIndex = this._model.getPartOffscreenIndices()[partIndex];
			if (offscreenIndex == -1) {
				const partsHierarchy = this._model.getPartsHierarchy();
				if (partsHierarchy && partsHierarchy[partIndex]) for (let i = 0; i < partsHierarchy[partIndex].objects.length; ++i) {
					const objectInfo = partsHierarchy[partIndex].objects[i];
					if (objectInfo.objectType === CubismModelObjectType.CubismModelObjectType_Drawable) {
						const drawableIndex = objectInfo.objectIndex;
						drawableColors[drawableIndex].color.r = r;
						drawableColors[drawableIndex].color.g = g;
						drawableColors[drawableIndex].color.b = b;
						drawableColors[drawableIndex].color.a = a;
					} else {
						const childPartIndex = objectInfo.objectIndex;
						this.setPartColor(childPartIndex, r, g, b, a, partColors, drawableColors, offscreenColors);
					}
				}
			} else {
				offscreenColors[offscreenIndex].color.r = r;
				offscreenColors[offscreenIndex].color.g = g;
				offscreenColors[offscreenIndex].color.b = b;
				offscreenColors[offscreenIndex].color.a = a;
			}
		}
	}
	/**
	* Sets the part color enabled flag with hierarchical propagation (internal method)
	*/
	setPartColorEnabled(partIndex, value, partColors, drawableColors, offscreenColors) {
		partColors[partIndex].isOverridden = value;
		const offscreenIndex = this._model.getPartOffscreenIndices()[partIndex];
		if (offscreenIndex == -1) {
			const partsHierarchy = this._model.getPartsHierarchy();
			if (partsHierarchy && partsHierarchy[partIndex]) for (let i = 0; i < partsHierarchy[partIndex].objects.length; ++i) {
				const objectInfo = partsHierarchy[partIndex].objects[i];
				if (objectInfo.objectType === CubismModelObjectType.CubismModelObjectType_Drawable) {
					const drawableIndex = objectInfo.objectIndex;
					drawableColors[drawableIndex].isOverridden = value;
					if (value) {
						drawableColors[drawableIndex].color.r = partColors[partIndex].color.r;
						drawableColors[drawableIndex].color.g = partColors[partIndex].color.g;
						drawableColors[drawableIndex].color.b = partColors[partIndex].color.b;
						drawableColors[drawableIndex].color.a = partColors[partIndex].color.a;
					}
				} else {
					const childPartIndex = objectInfo.objectIndex;
					if (value) {
						partColors[childPartIndex].color.r = partColors[partIndex].color.r;
						partColors[childPartIndex].color.g = partColors[partIndex].color.g;
						partColors[childPartIndex].color.b = partColors[partIndex].color.b;
						partColors[childPartIndex].color.a = partColors[partIndex].color.a;
					}
					this.setPartColorEnabled(childPartIndex, value, partColors, drawableColors, offscreenColors);
				}
			}
		} else {
			offscreenColors[offscreenIndex].isOverridden = value;
			if (value) {
				offscreenColors[offscreenIndex].color.r = partColors[partIndex].color.r;
				offscreenColors[offscreenIndex].color.g = partColors[partIndex].color.g;
				offscreenColors[offscreenIndex].color.b = partColors[partIndex].color.b;
				offscreenColors[offscreenIndex].color.a = partColors[partIndex].color.a;
			}
		}
	}
};
/**
* カラーブレンドのタイプ
*/
var CubismColorBlend = /* @__PURE__ */ function(CubismColorBlend) {
	CubismColorBlend[CubismColorBlend["ColorBlend_None"] = -1] = "ColorBlend_None";
	CubismColorBlend[CubismColorBlend["ColorBlend_Normal"] = Live2DCubismCore.ColorBlendType_Normal] = "ColorBlend_Normal";
	CubismColorBlend[CubismColorBlend["ColorBlend_AddGlow"] = Live2DCubismCore.ColorBlendType_AddGlow] = "ColorBlend_AddGlow";
	CubismColorBlend[CubismColorBlend["ColorBlend_Add"] = Live2DCubismCore.ColorBlendType_Add] = "ColorBlend_Add";
	CubismColorBlend[CubismColorBlend["ColorBlend_Darken"] = Live2DCubismCore.ColorBlendType_Darken] = "ColorBlend_Darken";
	CubismColorBlend[CubismColorBlend["ColorBlend_Multiply"] = Live2DCubismCore.ColorBlendType_Multiply] = "ColorBlend_Multiply";
	CubismColorBlend[CubismColorBlend["ColorBlend_ColorBurn"] = Live2DCubismCore.ColorBlendType_ColorBurn] = "ColorBlend_ColorBurn";
	CubismColorBlend[CubismColorBlend["ColorBlend_LinearBurn"] = Live2DCubismCore.ColorBlendType_LinearBurn] = "ColorBlend_LinearBurn";
	CubismColorBlend[CubismColorBlend["ColorBlend_Lighten"] = Live2DCubismCore.ColorBlendType_Lighten] = "ColorBlend_Lighten";
	CubismColorBlend[CubismColorBlend["ColorBlend_Screen"] = Live2DCubismCore.ColorBlendType_Screen] = "ColorBlend_Screen";
	CubismColorBlend[CubismColorBlend["ColorBlend_ColorDodge"] = Live2DCubismCore.ColorBlendType_ColorDodge] = "ColorBlend_ColorDodge";
	CubismColorBlend[CubismColorBlend["ColorBlend_Overlay"] = Live2DCubismCore.ColorBlendType_Overlay] = "ColorBlend_Overlay";
	CubismColorBlend[CubismColorBlend["ColorBlend_SoftLight"] = Live2DCubismCore.ColorBlendType_SoftLight] = "ColorBlend_SoftLight";
	CubismColorBlend[CubismColorBlend["ColorBlend_HardLight"] = Live2DCubismCore.ColorBlendType_HardLight] = "ColorBlend_HardLight";
	CubismColorBlend[CubismColorBlend["ColorBlend_LinearLight"] = Live2DCubismCore.ColorBlendType_LinearLight] = "ColorBlend_LinearLight";
	CubismColorBlend[CubismColorBlend["ColorBlend_Hue"] = Live2DCubismCore.ColorBlendType_Hue] = "ColorBlend_Hue";
	CubismColorBlend[CubismColorBlend["ColorBlend_Color"] = Live2DCubismCore.ColorBlendType_Color] = "ColorBlend_Color";
	CubismColorBlend[CubismColorBlend["ColorBlend_AddCompatible"] = Live2DCubismCore.ColorBlendType_AddCompatible] = "ColorBlend_AddCompatible";
	CubismColorBlend[CubismColorBlend["ColorBlend_MultiplyCompatible"] = Live2DCubismCore.ColorBlendType_MultiplyCompatible] = "ColorBlend_MultiplyCompatible";
	return CubismColorBlend;
}({});
/**
* アルファブレンドのタイプ
*/
var CubismAlphaBlend = /* @__PURE__ */ function(CubismAlphaBlend) {
	CubismAlphaBlend[CubismAlphaBlend["AlphaBlend_None"] = -1] = "AlphaBlend_None";
	CubismAlphaBlend[CubismAlphaBlend["AlphaBlend_Over"] = 0] = "AlphaBlend_Over";
	CubismAlphaBlend[CubismAlphaBlend["AlphaBlend_Atop"] = 1] = "AlphaBlend_Atop";
	CubismAlphaBlend[CubismAlphaBlend["AlphaBlend_Out"] = 2] = "AlphaBlend_Out";
	CubismAlphaBlend[CubismAlphaBlend["AlphaBlend_ConjointOver"] = 3] = "AlphaBlend_ConjointOver";
	CubismAlphaBlend[CubismAlphaBlend["AlphaBlend_DisjointOver"] = 4] = "AlphaBlend_DisjointOver";
	return CubismAlphaBlend;
}({});
/**
* オブジェクトのタイプ
*/
var CubismModelObjectType = /* @__PURE__ */ function(CubismModelObjectType) {
	CubismModelObjectType[CubismModelObjectType["CubismModelObjectType_Drawable"] = 0] = "CubismModelObjectType_Drawable";
	CubismModelObjectType[CubismModelObjectType["CubismModelObjectType_Parts"] = 1] = "CubismModelObjectType_Parts";
	return CubismModelObjectType;
}({});
/**
* Structure for managing the override of parameter repetition settings
*/
var ParameterRepeatData = class {
	/**
	* Constructor
	*
	* @param isOverridden whether to be overriden
	* @param isParameterRepeated override flag for settings
	*/
	constructor(isOverridden = false, isParameterRepeated = false) {
		this.isOverridden = isOverridden;
		this.isParameterRepeated = isParameterRepeated;
	}
};
/**
* テクスチャのカリング設定を管理するための構造体
*/
var CullingData = class {
	/**
	* コンストラクタ
	*
	* @param isOverridden
	* @param isCulling
	*/
	constructor(isOverridden = false, isCulling = false) {
		this.isOverridden = isOverridden;
		this.isCulling = isCulling;
	}
};
/**
* パーツ子描画オブジェクト情報構造体
*/
var PartChildDrawObjects = class {
	constructor(drawableIndices = new Array(), offscreenIndices = new Array()) {
		this.drawableIndices = drawableIndices;
		this.offscreenIndices = offscreenIndices;
	}
};
/**
* オブジェクト情報構造体
*/
var CubismModelObjectInfo = class {
	constructor(objectIndex, objectType) {
		this.objectIndex = objectIndex;
		this.objectType = objectType;
	}
};
/**
* パーツ情報管理構造体
*/
var CubismModelPartInfo = class {
	constructor(objects = new Array(), childDrawObjects = new PartChildDrawObjects()) {
		this.objects = objects;
		this.childDrawObjects = childDrawObjects;
	}
	getChildObjectCount() {
		return this.objects.length;
	}
};
/**
* モデル
*
* Mocデータから生成されるモデルのクラス。
*/
var CubismModel = class {
	/**
	* モデルのパラメータの更新
	*/
	update() {
		this._model.update();
		this._model.drawables.resetDynamicFlags();
	}
	/**
	* PixelsPerUnitを取得する
	* @return PixelsPerUnit
	*/
	getPixelsPerUnit() {
		if (this._model == null) return 0;
		return this._model.canvasinfo.PixelsPerUnit;
	}
	/**
	* キャンバスの幅を取得する
	*/
	getCanvasWidth() {
		if (this._model == null) return 0;
		return this._model.canvasinfo.CanvasWidth / this._model.canvasinfo.PixelsPerUnit;
	}
	/**
	* キャンバスの高さを取得する
	*/
	getCanvasHeight() {
		if (this._model == null) return 0;
		return this._model.canvasinfo.CanvasHeight / this._model.canvasinfo.PixelsPerUnit;
	}
	/**
	* パラメータを保存する
	*/
	saveParameters() {
		const parameterCount = this._model.parameters.count;
		const savedParameterCount = this._savedParameters.length;
		for (let i = 0; i < parameterCount; ++i) if (i < savedParameterCount) this._savedParameters[i] = this._parameterValues[i];
		else this._savedParameters.push(this._parameterValues[i]);
	}
	/**
	* 乗算色・スクリーン色管理クラスを取得する
	*
	* @return CubismModelMultiplyAndScreenColorのインスタンス
	*/
	getOverrideMultiplyAndScreenColor() {
		return this._overrideMultiplyAndScreenColor;
	}
	/**
	* Checks whether parameter repetition is performed for the entire model.
	*
	* @return true if parameter repetition is performed for the entire model; otherwise returns false.
	*/
	getOverrideFlagForModelParameterRepeat() {
		return this._isOverriddenParameterRepeat;
	}
	/**
	* Sets whether parameter repetition is performed for the entire model.
	* Use true to perform parameter repetition for the entire model, or false to not perform it.
	*/
	setOverrideFlagForModelParameterRepeat(isRepeat) {
		this._isOverriddenParameterRepeat = isRepeat;
	}
	/**
	* Returns the flag indicating whether to override the parameter repeat.
	*
	* @param parameterIndex Parameter index
	*
	* @return true if the parameter repeat is overridden, false otherwise.
	*/
	getOverrideFlagForParameterRepeat(parameterIndex) {
		return this._userParameterRepeatDataList[parameterIndex].isOverridden;
	}
	/**
	* Sets the flag indicating whether to override the parameter repeat.
	*
	* @param parameterIndex Parameter index
	* @param value true if it is to be overridden; otherwise, false.
	*/
	setOverrideFlagForParameterRepeat(parameterIndex, value) {
		this._userParameterRepeatDataList[parameterIndex].isOverridden = value;
	}
	/**
	* Returns the repeat flag.
	*
	* @param parameterIndex Parameter index
	*
	* @return true if repeating, false otherwise.
	*/
	getRepeatFlagForParameterRepeat(parameterIndex) {
		return this._userParameterRepeatDataList[parameterIndex].isParameterRepeated;
	}
	/**
	* Sets the repeat flag.
	*
	* @param parameterIndex Parameter index
	* @param value true to enable repeating, false otherwise.
	*/
	setRepeatFlagForParameterRepeat(parameterIndex, value) {
		this._userParameterRepeatDataList[parameterIndex].isParameterRepeated = value;
	}
	/**
	* Drawableのカリング情報を取得する。
	*
	* @param   drawableIndex   Drawableのインデックス
	*
	* @return  Drawableのカリング情報
	*/
	getDrawableCulling(drawableIndex) {
		if (this.getOverrideFlagForModelCullings() || this.getOverrideFlagForDrawableCullings(drawableIndex)) return this._userDrawableCullings[drawableIndex].isCulling;
		const constantFlags = this._model.drawables.constantFlags;
		return !Live2DCubismCore.Utils.hasIsDoubleSidedBit(constantFlags[drawableIndex]);
	}
	/**
	* Drawableのカリング情報を設定する。
	*
	* @param drawableIndex Drawableのインデックス
	* @param isCulling カリング情報
	*/
	setDrawableCulling(drawableIndex, isCulling) {
		this._userDrawableCullings[drawableIndex].isCulling = isCulling;
	}
	/**
	* Offscreenのカリング情報を取得する。
	*
	* @param   offscreenIndex   Offscreenのインデックス
	*
	* @return  Offscreenのカリング情報
	*/
	getOffscreenCulling(offscreenIndex) {
		if (this.getOverrideFlagForModelCullings() || this.getOverrideFlagForOffscreenCullings(offscreenIndex)) return this._userOffscreenCullings[offscreenIndex].isCulling;
		const constantFlags = this._model.offscreens.constantFlags;
		return !Live2DCubismCore.Utils.hasIsDoubleSidedBit(constantFlags[offscreenIndex]);
	}
	/**
	* Offscreenのカリング設定を設定する。
	*
	* @param offscreenIndex Offscreenのインデックス
	* @param isCulling カリング情報
	*/
	setOffscreenCulling(offscreenIndex, isCulling) {
		this._userOffscreenCullings[offscreenIndex].isCulling = isCulling;
	}
	/**
	* SDKからモデル全体のカリング設定を上書きするか。
	*
	* @return  true    ->  SDK上のカリング設定を使用
	*          false   ->  モデルのカリング設定を使用
	*/
	getOverrideFlagForModelCullings() {
		return this._isOverriddenCullings;
	}
	/**
	* SDKからモデル全体のカリング設定を上書きするかを設定する。
	*
	* @param isOverriddenCullings SDK上のカリング設定を使うならtrue、モデルのカリング設定を使うならfalse
	*/
	setOverrideFlagForModelCullings(isOverriddenCullings) {
		this._isOverriddenCullings = isOverriddenCullings;
	}
	/**
	*
	* @param drawableIndex Drawableのインデックス
	* @return  true    ->  SDK上のカリング設定を使用
	*          false   ->  モデルのカリング設定を使用
	*/
	getOverrideFlagForDrawableCullings(drawableIndex) {
		return this._userDrawableCullings[drawableIndex].isOverridden;
	}
	/**
	* @param offscreenIndex Offscreenのインデックス
	* @return  true    ->  SDK上のカリング設定を使用
	*          false   ->  モデルのカリング設定を使用
	*/
	getOverrideFlagForOffscreenCullings(offscreenIndex) {
		return this._userOffscreenCullings[offscreenIndex].isOverridden;
	}
	/**
	*
	* @param drawableIndex Drawableのインデックス
	* @param isOverriddenCullings SDK上のカリング設定を使うならtrue、モデルのカリング設定を使うならfalse
	*/
	setOverrideFlagForDrawableCullings(drawableIndex, isOverriddenCullings) {
		this._userDrawableCullings[drawableIndex].isOverridden = isOverriddenCullings;
	}
	/**
	* モデルの不透明度を取得する
	*
	* @return 不透明度の値
	*/
	getModelOapcity() {
		return this._modelOpacity;
	}
	/**
	* モデルの不透明度を設定する
	*
	* @param value 不透明度の値
	*/
	setModelOapcity(value) {
		this._modelOpacity = value;
	}
	/**
	* モデルを取得
	*/
	getModel() {
		return this._model;
	}
	/**
	* パーツのインデックスを取得
	* @param partId パーツのID
	* @return パーツのインデックス
	*/
	getPartIndex(partId) {
		let partIndex;
		const partCount = this._model.parts.count;
		for (partIndex = 0; partIndex < partCount; ++partIndex) if (partId == this._partIds[partIndex]) return partIndex;
		if (this._notExistPartId.has(partId)) return this._notExistPartId.get(partId);
		partIndex = partCount + this._notExistPartId.size;
		this._notExistPartId.set(partId, partIndex);
		this._notExistPartOpacities.set(partIndex, null);
		return partIndex;
	}
	/**
	* パーツのIDを取得する。
	*
	* @param partIndex 取得するパーツのインデックス
	* @return パーツのID
	*/
	getPartId(partIndex) {
		const partId = this._model.parts.ids[partIndex];
		return CubismFramework.getIdManager().getId(partId);
	}
	/**
	* パーツの個数の取得
	* @return パーツの個数
	*/
	getPartCount() {
		return this._model.parts.count;
	}
	/**
	* パーツのオフスクリーンインデックスの取得
	* @param partIndex パーツのインデックス
	* @return オフスクリーンインデックスのリスト
	*/
	getPartOffscreenIndices() {
		return this._model.parts.offscreenIndices;
	}
	/**
	* パーツの親パーツインデックスのリストを取得
	*
	* @return パーツの親パーツインデックスのリスト
	*/
	getPartParentPartIndices() {
		return this._model.parts.parentIndices;
	}
	/**
	* パーツの不透明度の設定(Index)
	* @param partIndex パーツのインデックス
	* @param opacity 不透明度
	*/
	setPartOpacityByIndex(partIndex, opacity) {
		if (this._notExistPartOpacities.has(partIndex)) {
			this._notExistPartOpacities.set(partIndex, opacity);
			return;
		}
		CSM_ASSERT(0 <= partIndex && partIndex < this.getPartCount());
		this._partOpacities[partIndex] = opacity;
	}
	/**
	* パーツの不透明度の設定(Id)
	* @param partId パーツのID
	* @param opacity パーツの不透明度
	*/
	setPartOpacityById(partId, opacity) {
		const index = this.getPartIndex(partId);
		if (index < 0) return;
		this.setPartOpacityByIndex(index, opacity);
	}
	/**
	* パーツの不透明度の取得(index)
	* @param partIndex パーツのインデックス
	* @return パーツの不透明度
	*/
	getPartOpacityByIndex(partIndex) {
		if (this._notExistPartOpacities.has(partIndex)) return this._notExistPartOpacities.get(partIndex);
		CSM_ASSERT(0 <= partIndex && partIndex < this.getPartCount());
		return this._partOpacities[partIndex];
	}
	/**
	* パーツの不透明度の取得(id)
	* @param partId パーツのＩｄ
	* @return パーツの不透明度
	*/
	getPartOpacityById(partId) {
		const index = this.getPartIndex(partId);
		if (index < 0) return 0;
		return this.getPartOpacityByIndex(index);
	}
	/**
	* パラメータのインデックスの取得
	* @param パラメータID
	* @return パラメータのインデックス
	*/
	getParameterIndex(parameterId) {
		let parameterIndex;
		const idCount = this._model.parameters.count;
		for (parameterIndex = 0; parameterIndex < idCount; ++parameterIndex) {
			if (parameterId != this._parameterIds[parameterIndex]) continue;
			return parameterIndex;
		}
		if (this._notExistParameterId.has(parameterId)) return this._notExistParameterId.get(parameterId);
		parameterIndex = this._model.parameters.count + this._notExistParameterId.size;
		this._notExistParameterId.set(parameterId, parameterIndex);
		this._notExistParameterValues.set(parameterIndex, null);
		return parameterIndex;
	}
	/**
	* パラメータの個数の取得
	* @return パラメータの個数
	*/
	getParameterCount() {
		return this._model.parameters.count;
	}
	/**
	* パラメータの種類の取得
	* @param parameterIndex パラメータのインデックス
	* @return csmParameterType_Normal -> 通常のパラメータ
	*          csmParameterType_BlendShape -> ブレンドシェイプパラメータ
	*/
	getParameterType(parameterIndex) {
		return this._model.parameters.types[parameterIndex];
	}
	/**
	* パラメータの最大値の取得
	* @param parameterIndex パラメータのインデックス
	* @return パラメータの最大値
	*/
	getParameterMaximumValue(parameterIndex) {
		return this._model.parameters.maximumValues[parameterIndex];
	}
	/**
	* パラメータの最小値の取得
	* @param parameterIndex パラメータのインデックス
	* @return パラメータの最小値
	*/
	getParameterMinimumValue(parameterIndex) {
		return this._model.parameters.minimumValues[parameterIndex];
	}
	/**
	* パラメータのデフォルト値の取得
	* @param parameterIndex パラメータのインデックス
	* @return パラメータのデフォルト値
	*/
	getParameterDefaultValue(parameterIndex) {
		return this._model.parameters.defaultValues[parameterIndex];
	}
	/**
	* 指定したパラメータindexのIDを取得
	*
	* @param parameterIndex パラメータのインデックス
	* @return パラメータID
	*/
	getParameterId(parameterIndex) {
		return CubismFramework.getIdManager().getId(this._model.parameters.ids[parameterIndex]);
	}
	/**
	* パラメータの値の取得
	* @param parameterIndex    パラメータのインデックス
	* @return パラメータの値
	*/
	getParameterValueByIndex(parameterIndex) {
		if (this._notExistParameterValues.has(parameterIndex)) return this._notExistParameterValues.get(parameterIndex);
		CSM_ASSERT(0 <= parameterIndex && parameterIndex < this.getParameterCount());
		return this._parameterValues[parameterIndex];
	}
	/**
	* パラメータの値の取得
	* @param parameterId    パラメータのID
	* @return パラメータの値
	*/
	getParameterValueById(parameterId) {
		const parameterIndex = this.getParameterIndex(parameterId);
		return this.getParameterValueByIndex(parameterIndex);
	}
	/**
	* パラメータの値の設定
	* @param parameterIndex パラメータのインデックス
	* @param value パラメータの値
	* @param weight 重み
	*/
	setParameterValueByIndex(parameterIndex, value, weight = 1) {
		if (this._notExistParameterValues.has(parameterIndex)) {
			this._notExistParameterValues.set(parameterIndex, weight == 1 ? value : this._notExistParameterValues.get(parameterIndex) * (1 - weight) + value * weight);
			return;
		}
		CSM_ASSERT(0 <= parameterIndex && parameterIndex < this.getParameterCount());
		if (this.isRepeat(parameterIndex)) value = this.getParameterRepeatValue(parameterIndex, value);
		else value = this.getParameterClampValue(parameterIndex, value);
		this._parameterValues[parameterIndex] = weight == 1 ? value : this._parameterValues[parameterIndex] = this._parameterValues[parameterIndex] * (1 - weight) + value * weight;
	}
	/**
	* パラメータの値の設定
	* @param parameterId パラメータのID
	* @param value パラメータの値
	* @param weight 重み
	*/
	setParameterValueById(parameterId, value, weight = 1) {
		const index = this.getParameterIndex(parameterId);
		this.setParameterValueByIndex(index, value, weight);
	}
	/**
	* パラメータの値の加算(index)
	* @param parameterIndex パラメータインデックス
	* @param value 加算する値
	* @param weight 重み
	*/
	addParameterValueByIndex(parameterIndex, value, weight = 1) {
		this.setParameterValueByIndex(parameterIndex, this.getParameterValueByIndex(parameterIndex) + value * weight);
	}
	/**
	* パラメータの値の加算(id)
	* @param parameterId パラメータＩＤ
	* @param value 加算する値
	* @param weight 重み
	*/
	addParameterValueById(parameterId, value, weight = 1) {
		const index = this.getParameterIndex(parameterId);
		this.addParameterValueByIndex(index, value, weight);
	}
	/**
	* Gets whether the parameter has the repeat setting.
	*
	* @param parameterIndex Parameter index
	*
	* @return true if it is set, otherwise returns false.
	*/
	isRepeat(parameterIndex) {
		if (this._notExistParameterValues.has(parameterIndex)) return false;
		CSM_ASSERT(0 <= parameterIndex && parameterIndex < this.getParameterCount());
		let isRepeat;
		if (this._isOverriddenParameterRepeat || this._userParameterRepeatDataList[parameterIndex].isOverridden) isRepeat = this._userParameterRepeatDataList[parameterIndex].isParameterRepeated;
		else isRepeat = this._model.parameters.repeats[parameterIndex] != 0;
		return isRepeat;
	}
	/**
	* Returns the calculated result ensuring the value falls within the parameter's range.
	*
	* @param parameterIndex Parameter index
	* @param value Parameter value
	*
	* @return a value that falls within the parameter’s range. If the parameter does not exist, returns it as is.
	*/
	getParameterRepeatValue(parameterIndex, value) {
		if (this._notExistParameterValues.has(parameterIndex)) return value;
		CSM_ASSERT(0 <= parameterIndex && parameterIndex < this.getParameterCount());
		const maxValue = this._model.parameters.maximumValues[parameterIndex];
		const minValue = this._model.parameters.minimumValues[parameterIndex];
		const valueSize = maxValue - minValue;
		if (maxValue < value) {
			const overValue = CubismMath.mod(value - maxValue, valueSize);
			if (!Number.isNaN(overValue)) value = minValue + overValue;
			else value = maxValue;
		}
		if (value < minValue) {
			const overValue = CubismMath.mod(minValue - value, valueSize);
			if (!Number.isNaN(overValue)) value = maxValue - overValue;
			else value = minValue;
		}
		return value;
	}
	/**
	* Returns the result of clamping the value to ensure it falls within the parameter's range.
	*
	* @param parameterIndex Parameter index
	* @param value Parameter value
	*
	* @return the clamped value. If the parameter does not exist, returns it as is.
	*/
	getParameterClampValue(parameterIndex, value) {
		if (this._notExistParameterValues.has(parameterIndex)) return value;
		CSM_ASSERT(0 <= parameterIndex && parameterIndex < this.getParameterCount());
		const maxValue = this._model.parameters.maximumValues[parameterIndex];
		const minValue = this._model.parameters.minimumValues[parameterIndex];
		return CubismMath.clamp(value, minValue, maxValue);
	}
	/**
	* Returns the repeat of the parameter.
	*
	* @param parameterIndex Parameter index
	*
	* @return the raw data parameter repeat from the Cubism Core.
	*/
	getParameterRepeats(parameterIndex) {
		return this._model.parameters.repeats[parameterIndex] != 0;
	}
	/**
	* パラメータの値の乗算
	* @param parameterId パラメータのID
	* @param value 乗算する値
	* @param weight 重み
	*/
	multiplyParameterValueById(parameterId, value, weight = 1) {
		const index = this.getParameterIndex(parameterId);
		this.multiplyParameterValueByIndex(index, value, weight);
	}
	/**
	* パラメータの値の乗算
	* @param parameterIndex パラメータのインデックス
	* @param value 乗算する値
	* @param weight 重み
	*/
	multiplyParameterValueByIndex(parameterIndex, value, weight = 1) {
		this.setParameterValueByIndex(parameterIndex, this.getParameterValueByIndex(parameterIndex) * (1 + (value - 1) * weight));
	}
	/**
	* Drawableのインデックスの取得
	* @param drawableId DrawableのID
	* @return Drawableのインデックス
	*/
	getDrawableIndex(drawableId) {
		const drawableCount = this._model.drawables.count;
		for (let drawableIndex = 0; drawableIndex < drawableCount; ++drawableIndex) if (this._drawableIds[drawableIndex] == drawableId) return drawableIndex;
		return -1;
	}
	/**
	* Drawableの個数の取得
	* @return drawableの個数
	*/
	getDrawableCount() {
		return this._model.drawables.count;
	}
	/**
	* DrawableのIDを取得する
	* @param drawableIndex Drawableのインデックス
	* @return drawableのID
	*/
	getDrawableId(drawableIndex) {
		const parameterIds = this._model.drawables.ids;
		return CubismFramework.getIdManager().getId(parameterIds[drawableIndex]);
	}
	/**
	* Drawableの描画順リストの取得
	* @return Drawableの描画順リスト
	*/
	getRenderOrders() {
		return this._model.getRenderOrders();
	}
	/**
	* Drawableのテクスチャインデックスの取得
	* @param drawableIndex Drawableのインデックス
	* @return drawableのテクスチャインデックス
	*/
	getDrawableTextureIndex(drawableIndex) {
		return this._model.drawables.textureIndices[drawableIndex];
	}
	/**
	* DrawableのVertexPositionsの変化情報の取得
	*
	* 直近のCubismModel.update関数でDrawableの頂点情報が変化したかを取得する。
	*
	* @param   drawableIndex   Drawableのインデックス
	* @return  true    Drawableの頂点情報が直近のCubismModel.update関数で変化した
	*          false   Drawableの頂点情報が直近のCubismModel.update関数で変化していない
	*/
	getDrawableDynamicFlagVertexPositionsDidChange(drawableIndex) {
		const dynamicFlags = this._model.drawables.dynamicFlags;
		return Live2DCubismCore.Utils.hasVertexPositionsDidChangeBit(dynamicFlags[drawableIndex]);
	}
	/**
	* Drawableの頂点インデックスの個数の取得
	* @param drawableIndex Drawableのインデックス
	* @return drawableの頂点インデックスの個数
	*/
	getDrawableVertexIndexCount(drawableIndex) {
		return this._model.drawables.indexCounts[drawableIndex];
	}
	/**
	* Drawableの頂点の個数の取得
	* @param drawableIndex Drawableのインデックス
	* @return drawableの頂点の個数
	*/
	getDrawableVertexCount(drawableIndex) {
		return this._model.drawables.vertexCounts[drawableIndex];
	}
	/**
	* Drawableの頂点リストの取得
	* @param drawableIndex drawableのインデックス
	* @return drawableの頂点リスト
	*/
	getDrawableVertices(drawableIndex) {
		return this.getDrawableVertexPositions(drawableIndex);
	}
	/**
	* Drawableの頂点インデックスリストの取得
	* @param drawableIndex Drawableのインデックス
	* @return drawableの頂点インデックスリスト
	*/
	getDrawableVertexIndices(drawableIndex) {
		return this._model.drawables.indices[drawableIndex];
	}
	/**
	* Drawableの頂点リストの取得
	* @param drawableIndex Drawableのインデックス
	* @return drawableの頂点リスト
	*/
	getDrawableVertexPositions(drawableIndex) {
		return this._model.drawables.vertexPositions[drawableIndex];
	}
	/**
	* Drawableの頂点のUVリストの取得
	* @param drawableIndex Drawableのインデックス
	* @return drawableの頂点UVリスト
	*/
	getDrawableVertexUvs(drawableIndex) {
		return this._model.drawables.vertexUvs[drawableIndex];
	}
	/**
	* Drawableの不透明度の取得
	* @param drawableIndex Drawableのインデックス
	* @return drawableの不透明度
	*/
	getDrawableOpacity(drawableIndex) {
		return this._model.drawables.opacities[drawableIndex];
	}
	/**
	* Drawableの乗算色の取得
	* @param drawableIndex Drawableのインデックス
	* @return drawableの乗算色(RGBA)
	* スクリーン色はRGBAで取得されるが、Aは必ず0
	*/
	getDrawableMultiplyColor(drawableIndex) {
		if (this._drawableMultiplyColors == null) {
			this._drawableMultiplyColors = new Array(this._model.drawables.count);
			this._drawableMultiplyColors.fill(new CubismTextureColor());
		}
		const multiplyColors = this._model.drawables.multiplyColors;
		const index = drawableIndex * 4;
		this._drawableMultiplyColors[drawableIndex].r = multiplyColors[index];
		this._drawableMultiplyColors[drawableIndex].g = multiplyColors[index + 1];
		this._drawableMultiplyColors[drawableIndex].b = multiplyColors[index + 2];
		this._drawableMultiplyColors[drawableIndex].a = multiplyColors[index + 3];
		return this._drawableMultiplyColors[drawableIndex];
	}
	/**
	* Drawableのスクリーン色の取得
	* @param drawableIndex Drawableのインデックス
	* @return drawableのスクリーン色(RGBA)
	* スクリーン色はRGBAで取得されるが、Aは必ず0
	*/
	getDrawableScreenColor(drawableIndex) {
		if (this._drawableScreenColors == null) {
			this._drawableScreenColors = new Array(this._model.drawables.count);
			this._drawableScreenColors.fill(new CubismTextureColor());
		}
		const screenColors = this._model.drawables.screenColors;
		const index = drawableIndex * 4;
		this._drawableScreenColors[drawableIndex].r = screenColors[index];
		this._drawableScreenColors[drawableIndex].g = screenColors[index + 1];
		this._drawableScreenColors[drawableIndex].b = screenColors[index + 2];
		this._drawableScreenColors[drawableIndex].a = screenColors[index + 3];
		return this._drawableScreenColors[drawableIndex];
	}
	/**
	* Offscreenの乗算色の取得
	* @param offscreenIndex Offscreenのインデックス
	* @return Offscreenの乗算色(RGBA)
	* スクリーン色はRGBAで取得されるが、Aは必ず0
	*/
	getOffscreenMultiplyColor(offscreenIndex) {
		if (this._offscreenMultiplyColors == null) {
			this._offscreenMultiplyColors = new Array(this._model.offscreens.count);
			this._offscreenMultiplyColors.fill(new CubismTextureColor());
		}
		const multiplyColors = this._model.offscreens.multiplyColors;
		const index = offscreenIndex * 4;
		this._offscreenMultiplyColors[offscreenIndex].r = multiplyColors[index];
		this._offscreenMultiplyColors[offscreenIndex].g = multiplyColors[index + 1];
		this._offscreenMultiplyColors[offscreenIndex].b = multiplyColors[index + 2];
		this._offscreenMultiplyColors[offscreenIndex].a = multiplyColors[index + 3];
		return this._offscreenMultiplyColors[offscreenIndex];
	}
	/**
	* Offscreenのスクリーン色の取得
	* @param offscreenIndex Offscreenのインデックス
	* @return Offscreenのスクリーン色(RGBA)
	* スクリーン色はRGBAで取得されるが、Aは必ず0
	*/
	getOffscreenScreenColor(offscreenIndex) {
		if (this._offscreenScreenColors == null) {
			this._offscreenScreenColors = new Array(this._model.offscreens.count);
			this._offscreenScreenColors.fill(new CubismTextureColor());
		}
		const screenColors = this._model.offscreens.screenColors;
		const index = offscreenIndex * 4;
		this._offscreenScreenColors[offscreenIndex].r = screenColors[index];
		this._offscreenScreenColors[offscreenIndex].g = screenColors[index + 1];
		this._offscreenScreenColors[offscreenIndex].b = screenColors[index + 2];
		this._offscreenScreenColors[offscreenIndex].a = screenColors[index + 3];
		return this._offscreenScreenColors[offscreenIndex];
	}
	/**
	* Drawableの親パーツのインデックスの取得
	* @param drawableIndex Drawableのインデックス
	* @return drawableの親パーツのインデックス
	*/
	getDrawableParentPartIndex(drawableIndex) {
		return this._model.drawables.parentPartIndices[drawableIndex];
	}
	/**
	* Drawableのブレンドモードを取得
	* @param drawableIndex Drawableのインデックス
	* @return drawableのブレンドモード
	*/
	getDrawableBlendMode(drawableIndex) {
		const constantFlags = this._model.drawables.constantFlags;
		return Live2DCubismCore.Utils.hasBlendAdditiveBit(constantFlags[drawableIndex]) ? CubismBlendMode.CubismBlendMode_Additive : Live2DCubismCore.Utils.hasBlendMultiplicativeBit(constantFlags[drawableIndex]) ? CubismBlendMode.CubismBlendMode_Multiplicative : CubismBlendMode.CubismBlendMode_Normal;
	}
	/**
	* Drawableのカラーブレンドの取得(Cubism 5.3 以降)
	*
	* @param drawableIndex Drawableのインデックス
	* @return Drawableのカラーブレンド
	*/
	getDrawableColorBlend(drawableIndex) {
		if (this._drawableColorBlends[drawableIndex] == -1) this._drawableColorBlends[drawableIndex] = this._model.drawables.blendModes[drawableIndex] & 255;
		return this._drawableColorBlends[drawableIndex];
	}
	/**
	* Drawableのアルファブレンドの取得(Cubism 5.3 以降)
	*
	* @param drawableIndex Drawableのインデックス
	* @return Drawableのアルファブレンド
	*/
	getDrawableAlphaBlend(drawableIndex) {
		if (this._drawableAlphaBlends[drawableIndex] == -1) this._drawableAlphaBlends[drawableIndex] = this._model.drawables.blendModes[drawableIndex] >> 8 & 255;
		return this._drawableAlphaBlends[drawableIndex];
	}
	/**
	* Drawableのマスクの反転使用の取得
	*
	* Drawableのマスク使用時の反転設定を取得する。
	* マスクを使用しない場合は無視される。
	*
	* @param drawableIndex Drawableのインデックス
	* @return Drawableの反転設定
	*/
	getDrawableInvertedMaskBit(drawableIndex) {
		const constantFlags = this._model.drawables.constantFlags;
		return Live2DCubismCore.Utils.hasIsInvertedMaskBit(constantFlags[drawableIndex]);
	}
	/**
	* Drawableのクリッピングマスクリストの取得
	* @return Drawableのクリッピングマスクリスト
	*/
	getDrawableMasks() {
		return this._model.drawables.masks;
	}
	/**
	* Drawableのクリッピングマスクの個数リストの取得
	* @return Drawableのクリッピングマスクの個数リスト
	*/
	getDrawableMaskCounts() {
		return this._model.drawables.maskCounts;
	}
	/**
	* クリッピングマスクの使用状態
	*
	* @return true クリッピングマスクを使用している
	* @return false クリッピングマスクを使用していない
	*/
	isUsingMasking() {
		for (let d = 0; d < this._model.drawables.count; ++d) {
			if (this._model.drawables.maskCounts[d] <= 0) continue;
			return true;
		}
		return false;
	}
	/**
	* Offscreenでクリッピングマスクを使用しているかどうかを取得
	*
	* @return true クリッピングマスクをオフスクリーンで使用している
	*/
	isUsingMaskingForOffscreen() {
		for (let d = 0; d < this.getOffscreenCount(); ++d) {
			if (this._model.offscreens.maskCounts[d] <= 0) continue;
			return true;
		}
		return false;
	}
	/**
	* Drawableの表示情報を取得する
	*
	* @param drawableIndex Drawableのインデックス
	* @return true Drawableが表示
	* @return false Drawableが非表示
	*/
	getDrawableDynamicFlagIsVisible(drawableIndex) {
		const dynamicFlags = this._model.drawables.dynamicFlags;
		return Live2DCubismCore.Utils.hasIsVisibleBit(dynamicFlags[drawableIndex]);
	}
	/**
	* DrawableのDrawOrderの変化情報の取得
	*
	* 直近のCubismModel.update関数でdrawableのdrawOrderが変化したかを取得する。
	* drawOrderはartMesh上で指定する0から1000の情報
	* @param drawableIndex drawableのインデックス
	* @return true drawableの不透明度が直近のCubismModel.update関数で変化した
	* @return false drawableの不透明度が直近のCubismModel.update関数で変化している
	*/
	getDrawableDynamicFlagVisibilityDidChange(drawableIndex) {
		const dynamicFlags = this._model.drawables.dynamicFlags;
		return Live2DCubismCore.Utils.hasVisibilityDidChangeBit(dynamicFlags[drawableIndex]);
	}
	/**
	* Drawableの不透明度の変化情報の取得
	*
	* 直近のCubismModel.update関数でdrawableの不透明度が変化したかを取得する。
	*
	* @param drawableIndex drawableのインデックス
	* @return true Drawableの不透明度が直近のCubismModel.update関数で変化した
	* @return false Drawableの不透明度が直近のCubismModel.update関数で変化してない
	*/
	getDrawableDynamicFlagOpacityDidChange(drawableIndex) {
		const dynamicFlags = this._model.drawables.dynamicFlags;
		return Live2DCubismCore.Utils.hasOpacityDidChangeBit(dynamicFlags[drawableIndex]);
	}
	/**
	* Drawableの描画順序の変化情報の取得
	*
	* 直近のCubismModel.update関数でDrawableの描画の順序が変化したかを取得する。
	*
	* @param drawableIndex Drawableのインデックス
	* @return true Drawableの描画の順序が直近のCubismModel.update関数で変化した
	* @return false Drawableの描画の順序が直近のCubismModel.update関数で変化してない
	*/
	getDrawableDynamicFlagRenderOrderDidChange(drawableIndex) {
		const dynamicFlags = this._model.drawables.dynamicFlags;
		return Live2DCubismCore.Utils.hasRenderOrderDidChangeBit(dynamicFlags[drawableIndex]);
	}
	/**
	* Drawableの乗算色・スクリーン色の変化情報の取得
	*
	* 直近のCubismModel.update関数でDrawableの乗算色・スクリーン色が変化したかを取得する。
	*
	* @param drawableIndex Drawableのインデックス
	* @return true Drawableの乗算色・スクリーン色が直近のCubismModel.update関数で変化した
	* @return false Drawableの乗算色・スクリーン色が直近のCubismModel.update関数で変化してない
	*/
	getDrawableDynamicFlagBlendColorDidChange(drawableIndex) {
		const dynamicFlags = this._model.drawables.dynamicFlags;
		return Live2DCubismCore.Utils.hasBlendColorDidChangeBit(dynamicFlags[drawableIndex]);
	}
	/**
	* オフスクリーンの個数を取得する
	* @return オフスクリーンの個数
	*/
	getOffscreenCount() {
		return this._model.offscreens.count;
	}
	/**
	* Offscreenのカラーブレンドの取得(Cubism 5.3 以降)
	*
	* @param offscreenIndex Offscreenのインデックス
	* @return Offscreenのカラーブレンド
	*/
	getOffscreenColorBlend(offscreenIndex) {
		if (this._offscreenColorBlends[offscreenIndex] == -1) this._offscreenColorBlends[offscreenIndex] = this._model.offscreens.blendModes[offscreenIndex] & 255;
		return this._offscreenColorBlends[offscreenIndex];
	}
	/**
	* Offscreenのアルファブレンドの取得(Cubism 5.3 以降)
	*
	* @param offscreenIndex Offscreenのインデックス
	* @return Offscreenのアルファブレンド
	*/
	getOffscreenAlphaBlend(offscreenIndex) {
		if (this._offscreenAlphaBlends[offscreenIndex] == -1) this._offscreenAlphaBlends[offscreenIndex] = this._model.offscreens.blendModes[offscreenIndex] >> 8 & 255;
		return this._offscreenAlphaBlends[offscreenIndex];
	}
	/**
	* オフスクリーンのオーナーインデックス配列を取得する
	* @return オフスクリーンのオーナーインデックス配列
	*/
	getOffscreenOwnerIndices() {
		return this._model.offscreens.ownerIndices;
	}
	/**
	* オフスクリーンの不透明度を取得
	* @param offscreenIndex オフスクリーンのインデックス
	* @return 不透明度
	*/
	getOffscreenOpacity(offscreenIndex) {
		if (offscreenIndex < 0 || offscreenIndex >= this._model.offscreens.count) return 1;
		return this._model.offscreens.opacities[offscreenIndex];
	}
	/**
	* オフスクリーンのクリッピングマスクリストの取得
	* @return オフスクリーンのクリッピングマスクリスト
	*/
	getOffscreenMasks() {
		return this._model.offscreens.masks;
	}
	/**
	* オフスクリーンのクリッピングマスクの個数リストの取得
	* @return オフスクリーンのクリッピングマスクの個数リスト
	*/
	getOffscreenMaskCounts() {
		return this._model.offscreens.maskCounts;
	}
	/**
	* オフスクリーンのマスク反転設定を取得する
	* @param offscreenIndex オフスクリーンのインデックス
	* @return オフスクリーンのマスク反転設定
	*/
	getOffscreenInvertedMask(offscreenIndex) {
		const constantFlags = this._model.offscreens.constantFlags;
		return Live2DCubismCore.Utils.hasIsInvertedMaskBit(constantFlags[offscreenIndex]);
	}
	/**
	* ブレンドモード使用判定
	* @return ブレンドモードを使用しているか
	*/
	isBlendModeEnabled() {
		return this._isBlendModeEnabled;
	}
	/**
	* 保存されたパラメータの読み込み
	*/
	loadParameters() {
		let parameterCount = this._model.parameters.count;
		const savedParameterCount = this._savedParameters.length;
		if (parameterCount > savedParameterCount) parameterCount = savedParameterCount;
		for (let i = 0; i < parameterCount; ++i) this._parameterValues[i] = this._savedParameters[i];
	}
	/**
	* 初期化する
	*/
	initialize() {
		CSM_ASSERT(this._model);
		this._parameterValues = this._model.parameters.values;
		this._partOpacities = this._model.parts.opacities;
		this._offscreenOpacities = this._model.offscreens.opacities;
		this._parameterMaximumValues = this._model.parameters.maximumValues;
		this._parameterMinimumValues = this._model.parameters.minimumValues;
		{
			const parameterIds = this._model.parameters.ids;
			const parameterCount = this._model.parameters.count;
			this._parameterIds.length = parameterCount;
			this._userParameterRepeatDataList.length = parameterCount;
			for (let i = 0; i < parameterCount; ++i) {
				this._parameterIds[i] = CubismFramework.getIdManager().getId(parameterIds[i]);
				this._userParameterRepeatDataList[i] = new ParameterRepeatData(false, false);
			}
		}
		const partCount = this._model.parts.count;
		{
			const partIds = this._model.parts.ids;
			this._partIds.length = partCount;
			for (let i = 0; i < partCount; ++i) this._partIds[i] = CubismFramework.getIdManager().getId(partIds[i]);
		}
		{
			const drawableIds = this._model.drawables.ids;
			const drawableCount = this._model.drawables.count;
			this._userDrawableCullings.length = drawableCount;
			const userCulling = new CullingData(false, false);
			this._userOffscreenCullings.length = this._model.offscreens.count;
			const userOffscreenCulling = new CullingData(false, false);
			for (let i = 0; i < drawableCount; ++i) {
				this._drawableIds.push(CubismFramework.getIdManager().getId(drawableIds[i]));
				this._userDrawableCullings[i] = userCulling;
			}
			for (let i = 0; i < this._model.offscreens.count; ++i) this._userOffscreenCullings[i] = userOffscreenCulling;
			if (this.getOffscreenCount() > 0) this._isBlendModeEnabled = true;
			else {
				this._model.drawables.blendModes;
				for (let i = 0; i < drawableCount; ++i) {
					const colorBlendType = this.getDrawableColorBlend(i);
					const alphaBlendType = this.getDrawableAlphaBlend(i);
					if (!(colorBlendType == CubismColorBlend.ColorBlend_Normal && alphaBlendType == 0) && colorBlendType != CubismColorBlend.ColorBlend_AddCompatible && colorBlendType != CubismColorBlend.ColorBlend_MultiplyCompatible) {
						this._isBlendModeEnabled = true;
						break;
					}
				}
			}
			this.setupPartsHierarchy();
			const offscreenCount = this.getOffscreenCount();
			this._overrideMultiplyAndScreenColor.initialize(partCount, drawableCount, offscreenCount);
		}
	}
	/**
	* パーツ階層構造を取得する
	* @return パーツ階層構造の配列
	*/
	getPartsHierarchy() {
		return this._partsHierarchy;
	}
	/**
	* パーツ階層構造をセットアップする
	*/
	setupPartsHierarchy() {
		this._partsHierarchy.length = 0;
		const partCount = this.getPartCount();
		this._partsHierarchy.length = partCount;
		for (let i = 0; i < partCount; ++i) {
			const partInfo = new CubismModelPartInfo();
			this._partsHierarchy[i] = partInfo;
		}
		for (let i = 0; i < partCount; ++i) {
			const parentPartIndex = this.getPartParentPartIndices()[i];
			if (parentPartIndex === -1) continue;
			for (let partIndex = 0; partIndex < this._partsHierarchy.length; ++partIndex) if (partIndex === parentPartIndex) {
				const objectInfo = new CubismModelObjectInfo(i, 1);
				this._partsHierarchy[partIndex].objects.push(objectInfo);
				break;
			}
		}
		const drawableCount = this.getDrawableCount();
		for (let i = 0; i < drawableCount; ++i) {
			const parentPartIndex = this.getDrawableParentPartIndex(i);
			if (parentPartIndex === -1) continue;
			for (let partIndex = 0; partIndex < this._partsHierarchy.length; ++partIndex) if (partIndex === parentPartIndex) {
				const objectInfo = new CubismModelObjectInfo(i, 0);
				this._partsHierarchy[partIndex].objects.push(objectInfo);
				break;
			}
		}
		for (let i = 0; i < this._partsHierarchy.length; ++i) this.getPartChildDrawObjects(i);
	}
	/**
	* 指定したパーツの子描画オブジェクト情報を取得・構築する
	* @param partInfoIndex パーツ情報のインデックス
	* @return PartChildDrawObjects
	*/
	getPartChildDrawObjects(partInfoIndex) {
		if (this._partsHierarchy[partInfoIndex].getChildObjectCount() < 1) return this._partsHierarchy[partInfoIndex].childDrawObjects;
		const childDrawObjects = this._partsHierarchy[partInfoIndex].childDrawObjects;
		if (childDrawObjects.drawableIndices.length !== 0 || childDrawObjects.offscreenIndices.length !== 0) return childDrawObjects;
		const objects = this._partsHierarchy[partInfoIndex].objects;
		for (let i = 0; i < objects.length; ++i) {
			const obj = objects[i];
			if (obj.objectType === 1) {
				this.getPartChildDrawObjects(obj.objectIndex);
				const childToChildDrawObjects = this._partsHierarchy[obj.objectIndex].childDrawObjects;
				childDrawObjects.drawableIndices.push(...childToChildDrawObjects.drawableIndices);
				childDrawObjects.offscreenIndices.push(...childToChildDrawObjects.offscreenIndices);
				const offscreenIndices = this.getOffscreenIndices();
				const offscreenIndex = offscreenIndices ? offscreenIndices[obj.objectIndex] : -1;
				if (offscreenIndex !== -1) childDrawObjects.offscreenIndices.push(offscreenIndex);
			} else if (obj.objectType === 0) childDrawObjects.drawableIndices.push(obj.objectIndex);
		}
		return childDrawObjects;
	}
	/**
	* パーツのオフスクリーンインデックス配列を取得
	* @return Int32Array offscreenIndices
	*/
	getOffscreenIndices() {
		return this._model.parts.offscreenIndices;
	}
	/**
	* コンストラクタ
	* @param model モデル
	*/
	constructor(model) {
		this._model = model;
		this._parameterValues = null;
		this._parameterMaximumValues = null;
		this._parameterMinimumValues = null;
		this._partOpacities = null;
		this._offscreenOpacities = null;
		this._savedParameters = new Array();
		this._parameterIds = new Array();
		this._drawableIds = new Array();
		this._partIds = new Array();
		this._isOverriddenParameterRepeat = true;
		this._isOverriddenCullings = false;
		this._modelOpacity = 1;
		this._overrideMultiplyAndScreenColor = new CubismModelMultiplyAndScreenColor(this);
		this._isBlendModeEnabled = false;
		this._drawableColorBlends = null;
		this._drawableAlphaBlends = null;
		this._offscreenColorBlends = null;
		this._offscreenAlphaBlends = null;
		this._drawableMultiplyColors = null;
		this._drawableScreenColors = null;
		this._offscreenMultiplyColors = null;
		this._offscreenScreenColors = null;
		this._userParameterRepeatDataList = new Array();
		this._userDrawableCullings = new Array();
		this._userOffscreenCullings = new Array();
		this._partsHierarchy = new Array();
		this._notExistPartId = /* @__PURE__ */ new Map();
		this._notExistParameterId = /* @__PURE__ */ new Map();
		this._notExistParameterValues = /* @__PURE__ */ new Map();
		this._notExistPartOpacities = /* @__PURE__ */ new Map();
		this._drawableColorBlends = new Array(model.drawables.count).fill(-1);
		this._drawableAlphaBlends = new Array(model.drawables.count).fill(-1);
		this._offscreenColorBlends = new Array(model.offscreens.count).fill(-1);
		this._offscreenAlphaBlends = new Array(model.offscreens.count).fill(-1);
	}
	/**
	* デストラクタ相当の処理
	*/
	release() {
		this._model.release();
		this._model = null;
		this._drawableColorBlends = null;
		this._drawableAlphaBlends = null;
		this._offscreenColorBlends = null;
		this._offscreenAlphaBlends = null;
		this._drawableMultiplyColors = null;
		this._drawableScreenColors = null;
		this._offscreenMultiplyColors = null;
		this._offscreenScreenColors = null;
	}
};
var Live2DCubismFramework$7;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismModel = CubismModel;
})(Live2DCubismFramework$7 || (Live2DCubismFramework$7 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/rendering/cubismclippingmanager.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
var ColorChannelCount = 4;
var ClippingMaskMaxCountOnDefault = 36;
var ClippingMaskMaxCountOnMultiRenderTexture = 32;
var CubismClippingManager = class {
	/**
	* コンストラクタ
	*/
	constructor(clippingContextFactory) {
		this._renderTextureCount = 0;
		this._clippingMaskBufferSize = 256;
		this._clippingContextListForMask = new Array();
		this._clippingContextListForDraw = new Array();
		this._clippingContextListForOffscreen = new Array();
		this._tmpBoundsOnModel = new csmRect();
		this._tmpMatrix = new CubismMatrix44();
		this._tmpMatrixForMask = new CubismMatrix44();
		this._tmpMatrixForDraw = new CubismMatrix44();
		this._clearedMaskBufferFlags = new Array();
		this._clippingContexttConstructor = clippingContextFactory;
		this._channelColors = [
			new CubismTextureColor(1, 0, 0, 0),
			new CubismTextureColor(0, 1, 0, 0),
			new CubismTextureColor(0, 0, 1, 0),
			new CubismTextureColor(0, 0, 0, 1)
		];
	}
	/**
	* デストラクタ相当の処理
	*/
	release() {
		for (let i = 0; i < this._clippingContextListForMask.length; i++) {
			if (this._clippingContextListForMask[i]) {
				this._clippingContextListForMask[i].release();
				this._clippingContextListForMask[i] = void 0;
			}
			this._clippingContextListForMask[i] = null;
		}
		this._clippingContextListForMask = null;
		for (let i = 0; i < this._clippingContextListForDraw.length; i++) this._clippingContextListForDraw[i] = null;
		this._clippingContextListForDraw = null;
		for (let i = 0; i < this._channelColors.length; i++) this._channelColors[i] = null;
		this._channelColors = null;
		if (this._clearedMaskBufferFlags != null) this._clearedMaskBufferFlags.length = 0;
		this._clearedMaskBufferFlags = null;
	}
	/**
	* マネージャの初期化処理
	* クリッピングマスクを使う描画オブジェクトの登録を行う
	* @param model モデルのインスタンス
	* @param renderTextureCount バッファの生成数
	*/
	initializeForDrawable(model, renderTextureCount) {
		if (renderTextureCount % 1 != 0) {
			CubismLogWarning("The number of render textures must be specified as an integer. The decimal point is rounded down and corrected to an integer.");
			renderTextureCount = ~~renderTextureCount;
		}
		if (renderTextureCount < 1) CubismLogWarning("The number of render textures must be an integer greater than or equal to 1. Set the number of render textures to 1.");
		this._renderTextureCount = renderTextureCount < 1 ? 1 : renderTextureCount;
		this._clearedMaskBufferFlags = new Array(this._renderTextureCount);
		this._clippingContextListForDraw.length = model.getDrawableCount();
		for (let i = 0; i < model.getDrawableCount(); i++) {
			if (model.getDrawableMaskCounts()[i] <= 0) {
				this._clippingContextListForDraw[i] = null;
				continue;
			}
			let clippingContext = this.findSameClip(model.getDrawableMasks()[i], model.getDrawableMaskCounts()[i]);
			if (clippingContext == null) {
				clippingContext = new this._clippingContexttConstructor(this, model.getDrawableMasks()[i], model.getDrawableMaskCounts()[i]);
				this._clippingContextListForMask.push(clippingContext);
			}
			clippingContext.addClippedDrawable(i);
			this._clippingContextListForDraw[i] = clippingContext;
		}
	}
	/**
	* オフスクリーン用の初期化処理
	*
	* @param model モデルのインスタンス
	* @param maskBufferCount オフスクリーン用のマスクバッファの数
	*/
	initializeForOffscreen(model, maskBufferCount) {
		this._renderTextureCount = maskBufferCount;
		this._clearedMaskBufferFlags.length = this._renderTextureCount;
		for (let i = 0; i < this._renderTextureCount; ++i) this._clearedMaskBufferFlags[i] = false;
		this._clippingContextListForOffscreen.length = model.getOffscreenCount();
		for (let i = 0; i < model.getOffscreenCount(); ++i) {
			if (model.getOffscreenMaskCounts()[i] <= 0) {
				this._clippingContextListForOffscreen.push(null);
				continue;
			}
			let cc = this.findSameClip(model.getOffscreenMasks()[i], model.getOffscreenMaskCounts()[i]);
			if (cc == null) {
				cc = new this._clippingContexttConstructor(this, model.getOffscreenMasks()[i], model.getOffscreenMaskCounts()[i]);
				this._clippingContextListForMask.push(cc);
			}
			cc.addClippedOffscreen(i);
			this._clippingContextListForOffscreen[i] = cc;
		}
	}
	/**
	* 既にマスクを作っているかを確認
	* 作っている様であれば該当するクリッピングマスクのインスタンスを返す
	* 作っていなければNULLを返す
	* @param drawableMasks 描画オブジェクトをマスクする描画オブジェクトのリスト
	* @param drawableMaskCounts 描画オブジェクトをマスクする描画オブジェクトの数
	* @return 該当するクリッピングマスクが存在すればインスタンスを返し、なければNULLを返す
	*/
	findSameClip(drawableMasks, drawableMaskCounts) {
		for (let i = 0; i < this._clippingContextListForMask.length; i++) {
			const clippingContext = this._clippingContextListForMask[i];
			const count = clippingContext._clippingIdCount;
			if (count != drawableMaskCounts) continue;
			let sameCount = 0;
			for (let j = 0; j < count; j++) {
				const clipId = clippingContext._clippingIdList[j];
				for (let k = 0; k < count; k++) if (drawableMasks[k] == clipId) {
					sameCount++;
					break;
				}
			}
			if (sameCount == count) return clippingContext;
		}
		return null;
	}
	/**
	* 高精細マスク処理用の行列を計算する
	* @param model モデルのインスタンス
	* @param isRightHanded 処理が右手系であるか
	*/
	setupMatrixForHighPrecision(model, isRightHanded) {
		let usingClipCount = 0;
		for (let clipIndex = 0; clipIndex < this._clippingContextListForMask.length; clipIndex++) {
			const cc = this._clippingContextListForMask[clipIndex];
			this.calcClippedDrawableTotalBounds(model, cc);
			if (cc._isUsing) usingClipCount++;
		}
		if (usingClipCount > 0) {
			this.setupLayoutBounds(0);
			if (this._clearedMaskBufferFlags.length != this._renderTextureCount) {
				this._clearedMaskBufferFlags.length = this._renderTextureCount;
				for (let i = 0; i < this._renderTextureCount; i++) this._clearedMaskBufferFlags[i] = false;
			} else for (let i = 0; i < this._renderTextureCount; i++) this._clearedMaskBufferFlags[i] = false;
			for (let clipIndex = 0; clipIndex < this._clippingContextListForMask.length; clipIndex++) {
				const clipContext = this._clippingContextListForMask[clipIndex];
				const allClippedDrawRect = clipContext._allClippedDrawRect;
				const layoutBoundsOnTex01 = clipContext._layoutBounds;
				const margin = .05;
				let scaleX = 0;
				let scaleY = 0;
				const ppu = model.getPixelsPerUnit();
				const maskPixelSize = clipContext.getClippingManager().getClippingMaskBufferSize();
				const physicalMaskWidth = layoutBoundsOnTex01.width * maskPixelSize;
				const physicalMaskHeight = layoutBoundsOnTex01.height * maskPixelSize;
				this._tmpBoundsOnModel.setRect(allClippedDrawRect);
				if (this._tmpBoundsOnModel.width * ppu > physicalMaskWidth) {
					this._tmpBoundsOnModel.expand(allClippedDrawRect.width * margin, 0);
					scaleX = layoutBoundsOnTex01.width / this._tmpBoundsOnModel.width;
				} else scaleX = ppu / physicalMaskWidth;
				if (this._tmpBoundsOnModel.height * ppu > physicalMaskHeight) {
					this._tmpBoundsOnModel.expand(0, allClippedDrawRect.height * margin);
					scaleY = layoutBoundsOnTex01.height / this._tmpBoundsOnModel.height;
				} else scaleY = ppu / physicalMaskHeight;
				this.createMatrixForMask(isRightHanded, layoutBoundsOnTex01, scaleX, scaleY);
				clipContext._matrixForMask.setMatrix(this._tmpMatrixForMask.getArray());
				clipContext._matrixForDraw.setMatrix(this._tmpMatrixForDraw.getArray());
			}
		}
	}
	/**
	* オフスクリーンの高精細マスク処理用の行列を計算する
	*
	* @param model モデルのインスタンス
	* @param isRightHanded 処理が右手系であるか
	* @param mvp モデルビュー投影行列
	*/
	setupMatrixForOffscreenHighPrecision(model, isRightHanded, mvp) {
		let usingClipCount = 0;
		for (let clipIndex = 0; clipIndex < this._clippingContextListForMask.length; clipIndex++) {
			const cc = this._clippingContextListForMask[clipIndex];
			this.calcClippedOffscreenTotalBounds(model, cc);
			if (cc._isUsing) usingClipCount++;
		}
		if (usingClipCount <= 0) return;
		this.setupLayoutBounds(0);
		if (this._clearedMaskBufferFlags.length != this._renderTextureCount) {
			this._clearedMaskBufferFlags.length = this._renderTextureCount;
			for (let i = 0; i < this._renderTextureCount; ++i) this._clearedMaskBufferFlags[i] = false;
		} else for (let i = 0; i < this._renderTextureCount; ++i) this._clearedMaskBufferFlags[i] = false;
		for (let clipIndex = 0; clipIndex < this._clippingContextListForMask.length; clipIndex++) {
			const clipContext = this._clippingContextListForMask[clipIndex];
			const allClippedDrawRect = clipContext._allClippedDrawRect;
			const layoutBoundsOnTex01 = clipContext._layoutBounds;
			const margin = .05;
			let scaleX = 0;
			let scaleY = 0;
			const ppu = model.getPixelsPerUnit();
			const maskPixel = clipContext.getClippingManager().getClippingMaskBufferSize();
			const physicalMaskWidth = layoutBoundsOnTex01.width * maskPixel;
			const physicalMaskHeight = layoutBoundsOnTex01.height * maskPixel;
			this._tmpBoundsOnModel.setRect(allClippedDrawRect);
			if (this._tmpBoundsOnModel.width * ppu > physicalMaskWidth) {
				this._tmpBoundsOnModel.expand(allClippedDrawRect.width * margin, 0);
				scaleX = layoutBoundsOnTex01.width / this._tmpBoundsOnModel.width;
			} else scaleX = ppu / physicalMaskWidth;
			if (this._tmpBoundsOnModel.height * ppu > physicalMaskHeight) {
				this._tmpBoundsOnModel.expand(0, allClippedDrawRect.height * margin);
				scaleY = layoutBoundsOnTex01.height / this._tmpBoundsOnModel.height;
			} else scaleY = ppu / physicalMaskHeight;
			this.createMatrixForMask(isRightHanded, layoutBoundsOnTex01, scaleX, scaleY);
			clipContext._matrixForMask.setMatrix(this._tmpMatrixForMask.getArray());
			clipContext._matrixForDraw.setMatrix(this._tmpMatrixForDraw.getArray());
			const invertMvp = mvp.getInvert();
			clipContext._matrixForDraw.multiplyByMatrix(invertMvp);
		}
	}
	/**
	* マスクを使う描画オブジェクトの全体の矩形を計算する。
	*
	* @param model モデルのインスタンス
	* @param clippingContext クリッピングコンテキスト
	*/
	calcClippedOffscreenTotalBounds(model, clippingContext) {
		let clippedDrawTotalMinX = Number.MAX_VALUE, clippedDrawTotalMinY = Number.MAX_VALUE;
		let clippedDrawTotalMaxX = -Number.MAX_VALUE, clippedDrawTotalMaxY = -Number.MAX_VALUE;
		const clippedOffscreenCount = clippingContext._clippedOffscreenIndexList.length;
		const clippedOffscreenChildDrawableIndexList = new Array();
		for (let clippedOffscreenIndex = 0; clippedOffscreenIndex < clippedOffscreenCount; clippedOffscreenIndex++) {
			const offscreenIndex = clippingContext._clippedOffscreenIndexList[clippedOffscreenIndex];
			this.getOffscreenChildDrawableIndexList(model, offscreenIndex, clippedOffscreenChildDrawableIndexList);
		}
		const childDrawableCount = clippedOffscreenChildDrawableIndexList.length;
		for (let childDrawableIndex = 0; childDrawableIndex < childDrawableCount; childDrawableIndex++) {
			const drawableVertexCount = model.getDrawableVertexCount(clippedOffscreenChildDrawableIndexList[childDrawableIndex]);
			const drawableVertexes = model.getDrawableVertices(clippedOffscreenChildDrawableIndexList[childDrawableIndex]);
			let minX = Number.MAX_VALUE, minY = Number.MAX_VALUE;
			let maxX = -Number.MAX_VALUE, maxY = -Number.MAX_VALUE;
			const loop = drawableVertexCount * Constant.vertexStep;
			for (let pi = Constant.vertexOffset; pi < loop; pi += Constant.vertexStep) {
				const x = drawableVertexes[pi];
				const y = drawableVertexes[pi + 1];
				if (x < minX) minX = x;
				if (x > maxX) maxX = x;
				if (y < minY) minY = y;
				if (y > maxY) maxY = y;
			}
			if (minX == Number.MAX_VALUE) continue;
			if (minX < clippedDrawTotalMinX) clippedDrawTotalMinX = minX;
			if (minY < clippedDrawTotalMinY) clippedDrawTotalMinY = minY;
			if (maxX > clippedDrawTotalMaxX) clippedDrawTotalMaxX = maxX;
			if (maxY > clippedDrawTotalMaxY) clippedDrawTotalMaxY = maxY;
		}
		if (clippedDrawTotalMinX == Number.MAX_VALUE) {
			clippingContext._allClippedDrawRect.x = 0;
			clippingContext._allClippedDrawRect.y = 0;
			clippingContext._allClippedDrawRect.width = 0;
			clippingContext._allClippedDrawRect.height = 0;
			clippingContext._isUsing = false;
		} else {
			clippingContext._isUsing = true;
			const w = clippedDrawTotalMaxX - clippedDrawTotalMinX;
			const h = clippedDrawTotalMaxY - clippedDrawTotalMinY;
			clippingContext._allClippedDrawRect.x = clippedDrawTotalMinX;
			clippingContext._allClippedDrawRect.y = clippedDrawTotalMinY;
			clippingContext._allClippedDrawRect.width = w;
			clippingContext._allClippedDrawRect.height = h;
		}
	}
	/**
	* マスクを使う描画オブジェクトの全体の矩形を計算する。
	*
	* @param model モデルのインスタンス
	* @param offscreenIndex オフスクリーンのインデックス
	* @param childDrawableIndexList オフスクリーンの子Drawableのインデックスリスト
	*/
	getOffscreenChildDrawableIndexList(model, offscreenIndex, childDrawableIndexList) {
		const ownerIndex = model.getOffscreenOwnerIndices()[offscreenIndex];
		this.getPartChildDrawableIndexList(model, ownerIndex, childDrawableIndexList);
	}
	/**
	* パーツの子Drawableのインデックスリストを取得する。
	*
	* @param model モデルのインスタンス
	* @param partIndex パーツのインデックス
	* @param childDrawableIndexList パーツの子Drawableのインデックスリスト
	*/
	getPartChildDrawableIndexList(model, partIndex, childDrawableIndexList) {
		const childDrawObjects = model.getPartsHierarchy()[partIndex].childDrawObjects;
		childDrawableIndexList.push(...childDrawObjects.drawableIndices);
		for (let i = 0; i < childDrawObjects.offscreenIndices.length; ++i) this.getOffscreenChildDrawableIndexList(model, childDrawObjects.offscreenIndices[i], childDrawableIndexList);
	}
	/**
	* マスク作成・描画用の行列を作成する。
	* @param isRightHanded 座標を右手系として扱うかを指定
	* @param layoutBoundsOnTex01 マスクを収める領域
	* @param scaleX 描画オブジェクトの伸縮率
	* @param scaleY 描画オブジェクトの伸縮率
	*/
	createMatrixForMask(isRightHanded, layoutBoundsOnTex01, scaleX, scaleY) {
		this._tmpMatrix.loadIdentity();
		this._tmpMatrix.translateRelative(-1, -1);
		this._tmpMatrix.scaleRelative(2, 2);
		this._tmpMatrix.translateRelative(layoutBoundsOnTex01.x, layoutBoundsOnTex01.y);
		this._tmpMatrix.scaleRelative(scaleX, scaleY);
		this._tmpMatrix.translateRelative(-this._tmpBoundsOnModel.x, -this._tmpBoundsOnModel.y);
		this._tmpMatrixForMask.setMatrix(this._tmpMatrix.getArray());
		this._tmpMatrix.loadIdentity();
		this._tmpMatrix.translateRelative(layoutBoundsOnTex01.x, layoutBoundsOnTex01.y * (isRightHanded ? -1 : 1));
		this._tmpMatrix.scaleRelative(scaleX, scaleY * (isRightHanded ? -1 : 1));
		this._tmpMatrix.translateRelative(-this._tmpBoundsOnModel.x, -this._tmpBoundsOnModel.y);
		this._tmpMatrixForDraw.setMatrix(this._tmpMatrix.getArray());
	}
	/**
	* クリッピングコンテキストを配置するレイアウト
	* 指定された数のレンダーテクスチャを極力いっぱいに使ってマスクをレイアウトする
	* マスクグループの数が4以下ならRGBA各チャンネルに一つずつマスクを配置し、5以上6以下ならRGBAを2,2,1,1と配置する。
	*
	* @param usingClipCount 配置するクリッピングコンテキストの数
	*/
	setupLayoutBounds(usingClipCount) {
		const useClippingMaskMaxCount = this._renderTextureCount <= 1 ? ClippingMaskMaxCountOnDefault : ClippingMaskMaxCountOnMultiRenderTexture * this._renderTextureCount;
		if (usingClipCount <= 0 || usingClipCount > useClippingMaskMaxCount) {
			if (usingClipCount > useClippingMaskMaxCount) CubismLogError("not supported mask count : {0}\n[Details] render texture count : {1}, mask count : {2}", usingClipCount - useClippingMaskMaxCount, this._renderTextureCount, usingClipCount);
			for (let index = 0; index < this._clippingContextListForMask.length; index++) {
				const clipContext = this._clippingContextListForMask[index];
				clipContext._layoutChannelIndex = 0;
				clipContext._layoutBounds.x = 0;
				clipContext._layoutBounds.y = 0;
				clipContext._layoutBounds.width = 1;
				clipContext._layoutBounds.height = 1;
				clipContext._bufferIndex = 0;
			}
			return;
		}
		const layoutCountMaxValue = this._renderTextureCount <= 1 ? 9 : 8;
		let countPerSheetDiv = usingClipCount / this._renderTextureCount;
		const reduceLayoutTextureCount = usingClipCount % this._renderTextureCount;
		countPerSheetDiv = Math.ceil(countPerSheetDiv);
		let divCount = countPerSheetDiv / ColorChannelCount;
		const modCount = countPerSheetDiv % ColorChannelCount;
		divCount = ~~divCount;
		let curClipIndex = 0;
		for (let renderTextureIndex = 0; renderTextureIndex < this._renderTextureCount; renderTextureIndex++) for (let channelIndex = 0; channelIndex < ColorChannelCount; channelIndex++) {
			let layoutCount = divCount + (channelIndex < modCount ? 1 : 0);
			const checkChannelIndex = modCount + (divCount < 1 ? -1 : 0);
			if (channelIndex == checkChannelIndex && reduceLayoutTextureCount > 0) layoutCount -= !(renderTextureIndex < reduceLayoutTextureCount) ? 1 : 0;
			if (layoutCount == 0) {} else if (layoutCount == 1) {
				const clipContext = this._clippingContextListForMask[curClipIndex++];
				clipContext._layoutChannelIndex = channelIndex;
				clipContext._layoutBounds.x = 0;
				clipContext._layoutBounds.y = 0;
				clipContext._layoutBounds.width = 1;
				clipContext._layoutBounds.height = 1;
				clipContext._bufferIndex = renderTextureIndex;
			} else if (layoutCount == 2) for (let i = 0; i < layoutCount; i++) {
				let xpos = i % 2;
				xpos = ~~xpos;
				const cc = this._clippingContextListForMask[curClipIndex++];
				cc._layoutChannelIndex = channelIndex;
				cc._layoutBounds.x = xpos * .5;
				cc._layoutBounds.y = 0;
				cc._layoutBounds.width = .5;
				cc._layoutBounds.height = 1;
				cc._bufferIndex = renderTextureIndex;
			}
			else if (layoutCount <= 4) for (let i = 0; i < layoutCount; i++) {
				let xpos = i % 2;
				let ypos = i / 2;
				xpos = ~~xpos;
				ypos = ~~ypos;
				const cc = this._clippingContextListForMask[curClipIndex++];
				cc._layoutChannelIndex = channelIndex;
				cc._layoutBounds.x = xpos * .5;
				cc._layoutBounds.y = ypos * .5;
				cc._layoutBounds.width = .5;
				cc._layoutBounds.height = .5;
				cc._bufferIndex = renderTextureIndex;
			}
			else if (layoutCount <= layoutCountMaxValue) for (let i = 0; i < layoutCount; i++) {
				let xpos = i % 3;
				let ypos = i / 3;
				xpos = ~~xpos;
				ypos = ~~ypos;
				const cc = this._clippingContextListForMask[curClipIndex++];
				cc._layoutChannelIndex = channelIndex;
				cc._layoutBounds.x = xpos / 3;
				cc._layoutBounds.y = ypos / 3;
				cc._layoutBounds.width = 1 / 3;
				cc._layoutBounds.height = 1 / 3;
				cc._bufferIndex = renderTextureIndex;
			}
			else {
				CubismLogError("not supported mask count : {0}\n[Details] render texture count : {1}, mask count : {2}", usingClipCount - useClippingMaskMaxCount, this._renderTextureCount, usingClipCount);
				for (let index = 0; index < layoutCount; index++) {
					const cc = this._clippingContextListForMask[curClipIndex++];
					cc._layoutChannelIndex = 0;
					cc._layoutBounds.x = 0;
					cc._layoutBounds.y = 0;
					cc._layoutBounds.width = 1;
					cc._layoutBounds.height = 1;
					cc._bufferIndex = 0;
				}
			}
		}
	}
	/**
	* マスクされる描画オブジェクト群全体を囲む矩形（モデル座標系）を計算する
	* @param model モデルのインスタンス
	* @param clippingContext クリッピングマスクのコンテキスト
	*/
	calcClippedDrawableTotalBounds(model, clippingContext) {
		let clippedDrawTotalMinX = Number.MAX_VALUE;
		let clippedDrawTotalMinY = Number.MAX_VALUE;
		let clippedDrawTotalMaxX = Number.MIN_VALUE;
		let clippedDrawTotalMaxY = Number.MIN_VALUE;
		const clippedDrawCount = clippingContext._clippedDrawableIndexList.length;
		for (let clippedDrawableIndex = 0; clippedDrawableIndex < clippedDrawCount; clippedDrawableIndex++) {
			const drawableIndex = clippingContext._clippedDrawableIndexList[clippedDrawableIndex];
			const drawableVertexCount = model.getDrawableVertexCount(drawableIndex);
			const drawableVertexes = model.getDrawableVertices(drawableIndex);
			let minX = Number.MAX_VALUE;
			let minY = Number.MAX_VALUE;
			let maxX = -Number.MAX_VALUE;
			let maxY = -Number.MAX_VALUE;
			const loop = drawableVertexCount * Constant.vertexStep;
			for (let pi = Constant.vertexOffset; pi < loop; pi += Constant.vertexStep) {
				const x = drawableVertexes[pi];
				const y = drawableVertexes[pi + 1];
				if (x < minX) minX = x;
				if (x > maxX) maxX = x;
				if (y < minY) minY = y;
				if (y > maxY) maxY = y;
			}
			if (minX == Number.MAX_VALUE) continue;
			if (minX < clippedDrawTotalMinX) clippedDrawTotalMinX = minX;
			if (minY < clippedDrawTotalMinY) clippedDrawTotalMinY = minY;
			if (maxX > clippedDrawTotalMaxX) clippedDrawTotalMaxX = maxX;
			if (maxY > clippedDrawTotalMaxY) clippedDrawTotalMaxY = maxY;
			if (clippedDrawTotalMinX == Number.MAX_VALUE) {
				clippingContext._allClippedDrawRect.x = 0;
				clippingContext._allClippedDrawRect.y = 0;
				clippingContext._allClippedDrawRect.width = 0;
				clippingContext._allClippedDrawRect.height = 0;
				clippingContext._isUsing = false;
			} else {
				clippingContext._isUsing = true;
				const w = clippedDrawTotalMaxX - clippedDrawTotalMinX;
				const h = clippedDrawTotalMaxY - clippedDrawTotalMinY;
				clippingContext._allClippedDrawRect.x = clippedDrawTotalMinX;
				clippingContext._allClippedDrawRect.y = clippedDrawTotalMinY;
				clippingContext._allClippedDrawRect.width = w;
				clippingContext._allClippedDrawRect.height = h;
			}
		}
	}
	/**
	* 画面描画に使用するクリッピングマスクのリストを取得する
	* @return 画面描画に使用するクリッピングマスクのリスト
	*/
	getClippingContextListForDraw() {
		return this._clippingContextListForDraw;
	}
	getClippingContextListForOffscreen() {
		return this._clippingContextListForOffscreen;
	}
	/**
	* クリッピングマスクバッファのサイズを取得する
	* @return クリッピングマスクバッファのサイズ
	*/
	getClippingMaskBufferSize() {
		return this._clippingMaskBufferSize;
	}
	/**
	* このバッファのレンダーテクスチャの枚数を取得する
	* @return このバッファのレンダーテクスチャの枚数
	*/
	getRenderTextureCount() {
		return this._renderTextureCount;
	}
	/**
	* カラーチャンネル（RGBA）のフラグを取得する
	* @param channelNo カラーチャンネル（RGBA）の番号（0:R, 1:G, 2:B, 3:A）
	*/
	getChannelFlagAsColor(channelNo) {
		return this._channelColors[channelNo];
	}
	/**
	* クリッピングマスクバッファのサイズを設定する
	* @param size クリッピングマスクバッファのサイズ
	*/
	setClippingMaskBufferSize(size) {
		this._clippingMaskBufferSize = size;
	}
};
// examples/packages/live2d_pet/vendor/Framework/src/rendering/cubismrendertarget_webgl.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
/**
* WebGL用オフスクリーンサーフェス
* マスクの描画に必要なフレームバッファなどを管理する。
*/
var CubismRenderTarget_WebGL = class {
	/**
	* WebGL2RenderingContext.blitFramebuffer() でバッファのコピーを行う。
	*
	* @param src コピー元のオフスクリーンサーフェス
	* @param dst コピー先のオフスクリーンサーフェス
	*/
	static copyBuffer(gl, src, dst) {
		if (src == null || dst == null) return;
		if (!(gl instanceof WebGL2RenderingContext)) throw new Error("WebGL2RenderingContext is required for buffer copy.");
		const previousFramebuffer = gl.getParameter(gl.FRAMEBUFFER_BINDING);
		gl.bindFramebuffer(gl.READ_FRAMEBUFFER, src.getRenderTexture());
		gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, dst.getRenderTexture());
		gl.blitFramebuffer(0, 0, src.getBufferWidth(), src.getBufferHeight(), 0, 0, dst.getBufferWidth(), dst.getBufferHeight(), gl.COLOR_BUFFER_BIT, gl.NEAREST);
		gl.bindFramebuffer(gl.FRAMEBUFFER, previousFramebuffer);
	}
	/**
	* 描画を開始する。
	*
	* @param restoreFbo EndDraw時に復元するFBOを指定する。nullを指定すると、beginDraw時に現在のFBOを記憶しておく。
	*/
	beginDraw(restoreFbo = null) {
		if (this._renderTexture == null) {
			console.error("_renderTexture is null");
			return;
		}
		if (restoreFbo == null) this._oldFbo = this._gl.getParameter(this._gl.FRAMEBUFFER_BINDING);
		else this._oldFbo = restoreFbo;
		this._gl.bindFramebuffer(this._gl.FRAMEBUFFER, this._renderTexture);
	}
	/**
	* 描画を終了し、バックバッファのサーフェイスを復元する。
	*/
	endDraw() {
		this._gl.bindFramebuffer(this._gl.FRAMEBUFFER, this._oldFbo);
	}
	/**
	* バインドされているカラーバッファのクリアを行う。
	*
	* @param r 赤の成分 (0.0 - 1.0)
	* @param g 緑の成分 (0.0 - 1.0)
	* @param b 青の成分 (0.0 - 1.0)
	* @param a アルファの成分 (0.0 - 1.0)
	*/
	clear(r, g, b, a) {
		this._gl.clearColor(r, g, b, a);
		this._gl.clear(this._gl.COLOR_BUFFER_BIT);
	}
	/**
	* オフスクリーンサーフェスを作成する。
	*
	* @param gl WebGLRenderingContextまたはWebGL2RenderingContext
	*          NOTE: Cubism 5.3以降のモデルが使用される場合はWebGL2RenderingContextを使用すること。
	* @param displayBufferWidth オフスクリーンサーフェスの幅
	* @param displayBufferHeight オフスクリーンサーフェスの高さ
	* @param previousFramebuffer 前のフレームバッファ
	*
	* @return 成功した場合はtrue、失敗した場合はfalse
	*/
	createRenderTarget(gl, displayBufferWidth, displayBufferHeight, previousFramebuffer) {
		this.destroyRenderTarget();
		this._colorBuffer = gl.createTexture();
		gl.bindTexture(gl.TEXTURE_2D, this._colorBuffer);
		gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, displayBufferWidth, displayBufferHeight, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
		gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
		gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
		gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
		gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
		gl.bindTexture(gl.TEXTURE_2D, null);
		const ret = gl.createFramebuffer();
		if (ret == null) {
			CubismLogError("Failed to create framebuffer");
			return false;
		}
		gl.bindFramebuffer(gl.FRAMEBUFFER, ret);
		gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, this._colorBuffer, 0);
		if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) {
			CubismLogError("Framebuffer is not complete");
			gl.bindFramebuffer(gl.FRAMEBUFFER, previousFramebuffer);
			gl.deleteFramebuffer(ret);
			this.destroyRenderTarget();
			return false;
		}
		this._renderTexture = ret;
		this._bufferWidth = displayBufferWidth;
		this._bufferHeight = displayBufferHeight;
		this._gl = gl;
		return true;
	}
	/**
	* レンダーターゲットを破棄する。
	*/
	destroyRenderTarget() {
		if (this._colorBuffer) {
			this._gl.bindTexture(this._gl.TEXTURE_2D, null);
			this._gl.deleteTexture(this._colorBuffer);
			this._colorBuffer = null;
		}
		if (this._renderTexture) {
			this._gl.bindFramebuffer(this._gl.FRAMEBUFFER, null);
			this._gl.deleteFramebuffer(this._renderTexture);
			this._renderTexture = null;
		}
	}
	/**
	* WebGLのコンテキストを取得する。
	*
	* @return WebGLRenderingContextまたはWebGL2RenderingContext
	*/
	getGL() {
		return this._gl;
	}
	/**
	* レンダーテクスチャを取得する。
	*
	* @return WebGLFramebuffer
	*/
	getRenderTexture() {
		return this._renderTexture;
	}
	/**
	* カラーバッファを取得する。
	*
	* @return WebGLTexture
	*/
	getColorBuffer() {
		return this._colorBuffer;
	}
	/**
	* カラーバッファの幅を取得する。
	*
	* @return カラーバッファの幅
	*/
	getBufferWidth() {
		return this._bufferWidth;
	}
	/**
	* カラーバッファの高さを取得する。
	*
	* @return カラーバッファの高さ
	*/
	getBufferHeight() {
		return this._bufferHeight;
	}
	/**
	* オフスクリーンサーフェスが有効かどうかを確認する。
	*
	* @return 有効な場合はtrue、無効な場合はfalse
	*/
	isValid() {
		return this._renderTexture != null;
	}
	/**
	* 以前のフレームバッファを取得する。
	*
	* @return 以前のフレームバッファ
	*/
	getOldFBO() {
		return this._oldFbo;
	}
	/**
	* コンストラクタ
	*/
	constructor() {
		this._gl = null;
		this._colorBuffer = null;
		this._renderTexture = null;
		this._bufferWidth = 0;
		this._bufferHeight = 0;
		this._oldFbo = null;
	}
};
var Live2DCubismFramework$6;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismOffscreenSurface_WebGL = CubismRenderTarget_WebGL;
})(Live2DCubismFramework$6 || (Live2DCubismFramework$6 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/rendering/cubismshader_webgl.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
var VertShaderSrcPath = "vertshadersrc.vert";
var VertShaderSrcMaskedPath = "vertshadersrcmasked.vert";
var VertShaderSrcSetupMaskPath = "vertshadersrcsetupmask.vert";
var FragShaderSrcSetupMaskPath = "fragshadersrcsetupmask.frag";
var FragShaderSrcPremultipliedAlphaPath = "fragshadersrcpremultipliedalpha.frag";
var FragShaderSrcMaskPremultipliedAlphaPath = "fragshadersrcmaskpremultipliedalpha.frag";
var FragShaderSrcMaskInvertedPremultipliedAlphaPath = "fragshadersrcmaskinvertedpremultipliedalpha.frag";
var VertShaderSrcCopyPath = "vertshadersrccopy.vert";
var FragShaderSrcCopyPath = "fragshadersrccopy.frag";
var FragShaderSrcColorBlendPath = "fragshadersrccolorblend.frag";
var FragShaderSrcAlphaBlendPath = "fragshadersrcalphablend.frag";
var VertShaderSrcBlendPath = "vertshadersrcblend.vert";
var FragShaderSrcBlendPath = "fragshadersrcpremultipliedalphablend.frag";
var ColorBlendPrefix = "ColorBlend_";
var AlphaBlendPrefix = "AlphaBlend_";
var s_instance;
var s_renderTargetVertexArray = new Float32Array([
	-1,
	-1,
	1,
	-1,
	-1,
	1,
	1,
	1
]);
var s_renderTargetUvArray = new Float32Array([
	0,
	0,
	1,
	0,
	0,
	1,
	1,
	1
]);
var s_renderTargetReverseUvArray = new Float32Array([
	0,
	1,
	1,
	1,
	0,
	0,
	1,
	0
]);
/**
* WebGL用のシェーダープログラムを生成・破棄するクラス
*/
var CubismShader_WebGL = class {
	/**
	* 非同期でシェーダーをパスから読み込む
	*
	* @param url シェーダーのURL
	*
	* @return シェーダーのソースコード
	*/
	async loadShader(url) {
		return await (await fetch(url)).text();
	}
	/**
	* ブレンドモード用のシェーダーを読み込む
	*/
	async loadShaders() {
		const shaderDir = this._shaderPath ?? this._defaultShaderPath;
		const shaderFiles = [
			{
				path: shaderDir + VertShaderSrcPath,
				prop: "_vertShaderSrc"
			},
			{
				path: shaderDir + VertShaderSrcMaskedPath,
				prop: "_vertShaderSrcMasked"
			},
			{
				path: shaderDir + VertShaderSrcSetupMaskPath,
				prop: "_vertShaderSrcSetupMask"
			},
			{
				path: shaderDir + FragShaderSrcSetupMaskPath,
				prop: "_fragShaderSrcSetupMask"
			},
			{
				path: shaderDir + FragShaderSrcPremultipliedAlphaPath,
				prop: "_fragShaderSrcPremultipliedAlpha"
			},
			{
				path: shaderDir + FragShaderSrcMaskPremultipliedAlphaPath,
				prop: "_fragShaderSrcMaskPremultipliedAlpha"
			},
			{
				path: shaderDir + FragShaderSrcMaskInvertedPremultipliedAlphaPath,
				prop: "_fragShaderSrcMaskInvertedPremultipliedAlpha"
			},
			{
				path: shaderDir + VertShaderSrcCopyPath,
				prop: "_vertShaderSrcCopy"
			},
			{
				path: shaderDir + FragShaderSrcCopyPath,
				prop: "_fragShaderSrcCopy"
			},
			{
				path: shaderDir + FragShaderSrcColorBlendPath,
				prop: "_fragShaderSrcColorBlend"
			},
			{
				path: shaderDir + FragShaderSrcAlphaBlendPath,
				prop: "_fragShaderSrcAlphaBlend"
			},
			{
				path: shaderDir + VertShaderSrcBlendPath,
				prop: "_vertShaderSrcBlend"
			},
			{
				path: shaderDir + FragShaderSrcBlendPath,
				prop: "_fragShaderSrcBlend"
			}
		];
		(await Promise.all(shaderFiles.map((file) => this.loadShader(file.path).then((data) => ({
			prop: file.prop,
			data
		})).catch((error) => {
			console.error(`Error loading ${file.path} shader:`, error);
			return {
				prop: file.prop,
				data: ""
			};
		})))).forEach((result) => {
			this[result.prop] = result.data;
		});
	}
	/**
	* コンストラクタ
	*/
	constructor() {
		this._shaderSets = new Array();
		this._isShaderLoading = false;
		this._isShaderLoaded = false;
		this._colorBlendMap = /* @__PURE__ */ new Map();
		this._colorBlendValues = new Array();
		const colorBlendKeys = Object.keys(CubismColorBlend);
		const colorBlendRawValues = Object.keys(CubismColorBlend).map((k) => CubismColorBlend[k]);
		for (let i = 0; i < colorBlendKeys.length; i++) {
			const colorBlendKey = colorBlendKeys[i];
			if (colorBlendKey.includes(ColorBlendPrefix)) {
				const blendModeName = colorBlendKey.slice(11);
				const colorBlendNumber = parseInt(colorBlendRawValues[i].toString());
				this._colorBlendMap.set(colorBlendNumber, blendModeName);
				this._colorBlendValues.push(colorBlendNumber);
			}
		}
		this._alphaBlendMap = /* @__PURE__ */ new Map();
		this._alphaBlendValues = new Array();
		const alphaBlendKeys = Object.keys(CubismAlphaBlend);
		const alphaBlendRawValues = Object.keys(CubismAlphaBlend).map((k) => CubismAlphaBlend[k]);
		for (let i = 0; i < alphaBlendKeys.length; i++) {
			const alphaBlendKey = alphaBlendKeys[i];
			if (alphaBlendKey.includes(AlphaBlendPrefix)) {
				const blendModeName = alphaBlendKey.slice(11);
				const alphaBlendNumber = parseInt(alphaBlendRawValues[i].toString());
				this._alphaBlendMap.set(alphaBlendNumber, blendModeName);
				this._alphaBlendValues.push(alphaBlendNumber);
			}
		}
		this._blendShaderSetMap = /* @__PURE__ */ new Map();
		this._shaderCount = 11 + (this._colorBlendValues.length - 3) * (this._alphaBlendValues.length - 1) * 3;
		this._defaultShaderPath = "../../Framework/Shaders/WebGL/";
		this._shaderPath = this._defaultShaderPath;
	}
	/**
	* デストラクタ相当の処理
	*/
	release() {
		this.releaseShaderProgram();
	}
	/**
	* 描画用のシェーダプログラムの一連のセットアップを実行する
	*
	* @param renderer レンダラー
	* @param model 描画対象のモデル
	* @param index 描画対象のメッシュのインデックス
	*/
	setupShaderProgramForDrawable(renderer, model, index) {
		if (!renderer.isPremultipliedAlpha()) CubismLogError("NoPremultipliedAlpha is not allowed");
		if (this._shaderSets.length == 0) this.generateShaders();
		if (this._isShaderLoaded == false) {
			CubismLogWarning("Shader program is not initialized.");
			return;
		}
		let srcColor;
		let dstColor;
		let srcAlpha;
		let dstAlpha;
		const masked = renderer.getClippingContextBufferForDrawable() != null;
		const invertedMask = model.getDrawableInvertedMaskBit(index);
		const offset = masked ? invertedMask ? 2 : 1 : 0;
		let shaderSet;
		let isUsingCompatible = true;
		if (model.isBlendModeEnabled()) {
			const colorBlendMode = model.getDrawableColorBlend(index);
			const alphaBlendMode = model.getDrawableAlphaBlend(index);
			if (colorBlendMode == CubismColorBlend.ColorBlend_None || alphaBlendMode == CubismAlphaBlend.AlphaBlend_None || colorBlendMode == CubismColorBlend.ColorBlend_Normal && alphaBlendMode == CubismAlphaBlend.AlphaBlend_Over) {
				shaderSet = this._shaderSets[1 + offset];
				srcColor = this.gl.ONE;
				dstColor = this.gl.ONE_MINUS_SRC_ALPHA;
				srcAlpha = this.gl.ONE;
				dstAlpha = this.gl.ONE_MINUS_SRC_ALPHA;
			} else switch (colorBlendMode) {
				case CubismColorBlend.ColorBlend_AddCompatible:
					shaderSet = this._shaderSets[4 + offset];
					srcColor = this.gl.ONE;
					dstColor = this.gl.ONE;
					srcAlpha = this.gl.ZERO;
					dstAlpha = this.gl.ONE;
					break;
				case CubismColorBlend.ColorBlend_MultiplyCompatible:
					shaderSet = this._shaderSets[7 + offset];
					srcColor = this.gl.DST_COLOR;
					dstColor = this.gl.ONE_MINUS_SRC_ALPHA;
					srcAlpha = this.gl.ZERO;
					dstAlpha = this.gl.ONE;
					break;
				default: {
					const srcBuffer = renderer._currentOffscreen != null ? renderer._currentOffscreen : renderer.getModelRenderTarget(0);
					CubismRenderTarget_WebGL.copyBuffer(this.gl, srcBuffer, renderer.getModelRenderTarget(1));
					const baseShaderSetIndex = this._blendShaderSetMap.get(this._colorBlendMap.get(colorBlendMode) + this._alphaBlendMap.get(alphaBlendMode));
					shaderSet = this._shaderSets[baseShaderSetIndex + offset];
					srcColor = this.gl.ONE;
					dstColor = this.gl.ZERO;
					srcAlpha = this.gl.ONE;
					dstAlpha = this.gl.ZERO;
					isUsingCompatible = false;
				}
			}
		} else switch (model.getDrawableBlendMode(index)) {
			case CubismBlendMode.CubismBlendMode_Normal:
			default:
				shaderSet = this._shaderSets[1 + offset];
				srcColor = this.gl.ONE;
				dstColor = this.gl.ONE_MINUS_SRC_ALPHA;
				srcAlpha = this.gl.ONE;
				dstAlpha = this.gl.ONE_MINUS_SRC_ALPHA;
				break;
			case CubismBlendMode.CubismBlendMode_Additive:
				shaderSet = this._shaderSets[4 + offset];
				srcColor = this.gl.ONE;
				dstColor = this.gl.ONE;
				srcAlpha = this.gl.ZERO;
				dstAlpha = this.gl.ONE;
				break;
			case CubismBlendMode.CubismBlendMode_Multiplicative:
				shaderSet = this._shaderSets[7 + offset];
				srcColor = this.gl.DST_COLOR;
				dstColor = this.gl.ONE_MINUS_SRC_ALPHA;
				srcAlpha = this.gl.ZERO;
				dstAlpha = this.gl.ONE;
		}
		this.gl.useProgram(shaderSet.shaderProgram);
		if (renderer._bufferData.vertex == null) renderer._bufferData.vertex = this.gl.createBuffer();
		this.gl.bindBuffer(this.gl.ARRAY_BUFFER, renderer._bufferData.vertex);
		const vertexArray = model.getDrawableVertices(index);
		this.gl.bufferData(this.gl.ARRAY_BUFFER, vertexArray, this.gl.DYNAMIC_DRAW);
		this.gl.enableVertexAttribArray(shaderSet.attributePositionLocation);
		this.gl.vertexAttribPointer(shaderSet.attributePositionLocation, 2, this.gl.FLOAT, false, 0, 0);
		if (renderer._bufferData.uv == null) renderer._bufferData.uv = this.gl.createBuffer();
		this.gl.bindBuffer(this.gl.ARRAY_BUFFER, renderer._bufferData.uv);
		const uvArray = model.getDrawableVertexUvs(index);
		this.gl.bufferData(this.gl.ARRAY_BUFFER, uvArray, this.gl.DYNAMIC_DRAW);
		this.gl.enableVertexAttribArray(shaderSet.attributeTexCoordLocation);
		this.gl.vertexAttribPointer(shaderSet.attributeTexCoordLocation, 2, this.gl.FLOAT, false, 0, 0);
		if (masked) {
			this.gl.activeTexture(this.gl.TEXTURE1);
			const tex = renderer.getDrawableMaskBuffer(renderer.getClippingContextBufferForDrawable()._bufferIndex).getColorBuffer();
			this.gl.bindTexture(this.gl.TEXTURE_2D, tex);
			this.gl.uniform1i(shaderSet.samplerTexture1Location, 1);
			this.gl.uniformMatrix4fv(shaderSet.uniformClipMatrixLocation, false, renderer.getClippingContextBufferForDrawable()._matrixForDraw.getArray());
			const channelIndex = renderer.getClippingContextBufferForDrawable()._layoutChannelIndex;
			const colorChannel = renderer.getClippingContextBufferForDrawable().getClippingManager().getChannelFlagAsColor(channelIndex);
			this.gl.uniform4f(shaderSet.uniformChannelFlagLocation, colorChannel.r, colorChannel.g, colorChannel.b, colorChannel.a);
			if (model.isBlendModeEnabled()) this.gl.uniform1f(shaderSet.uniformInvertMaskFlagLocation, invertedMask ? 1 : 0);
		}
		const textureNo = model.getDrawableTextureIndex(index);
		const textureId = renderer.getBindedTextures().get(textureNo);
		this.gl.activeTexture(this.gl.TEXTURE0);
		this.gl.bindTexture(this.gl.TEXTURE_2D, textureId);
		this.gl.uniform1i(shaderSet.samplerTexture0Location, 0);
		const matrix4x4 = renderer.getMvpMatrix();
		this.gl.uniformMatrix4fv(shaderSet.uniformMatrixLocation, false, matrix4x4.getArray());
		let baseColor = null;
		if (model.isBlendModeEnabled()) {
			const drawableOpacity = model.getDrawableOpacity(index);
			baseColor = new CubismTextureColor(drawableOpacity, drawableOpacity, drawableOpacity, drawableOpacity);
		} else baseColor = renderer.getModelColorWithOpacity(model.getDrawableOpacity(index));
		const multiplyAndScreenColor = model.getOverrideMultiplyAndScreenColor();
		const multiplyColor = multiplyAndScreenColor.getDrawableMultiplyColor(index);
		const screenColor = multiplyAndScreenColor.getDrawableScreenColor(index);
		this.gl.uniform4f(shaderSet.uniformBaseColorLocation, baseColor.r, baseColor.g, baseColor.b, baseColor.a);
		this.gl.uniform4f(shaderSet.uniformMultiplyColorLocation, multiplyColor.r, multiplyColor.g, multiplyColor.b, multiplyColor.a);
		this.gl.uniform4f(shaderSet.uniformScreenColorLocation, screenColor.r, screenColor.g, screenColor.b, screenColor.a);
		if (model.isBlendModeEnabled()) {
			this.gl.activeTexture(this.gl.TEXTURE2);
			if (!isUsingCompatible) {
				const tex = renderer.getModelRenderTarget(1).getColorBuffer();
				this.gl.bindTexture(this.gl.TEXTURE_2D, tex);
				this.gl.uniform1i(shaderSet.samplerFrameBufferTextureLocation, 2);
			}
		}
		if (renderer._bufferData.index == null) renderer._bufferData.index = this.gl.createBuffer();
		const indexArray = model.getDrawableVertexIndices(index);
		this.gl.bindBuffer(this.gl.ELEMENT_ARRAY_BUFFER, renderer._bufferData.index);
		this.gl.bufferData(this.gl.ELEMENT_ARRAY_BUFFER, indexArray, this.gl.DYNAMIC_DRAW);
		this.gl.blendFuncSeparate(srcColor, dstColor, srcAlpha, dstAlpha);
	}
	/**
	* オフスクリーン用のシェーダプログラムの一連のセットアップを実行する
	*
	* @param renderer レンダラー
	* @param model 描画対象のモデル
	* @param offscreen 描画対象のオフスクリーン
	*/
	setupShaderProgramForOffscreen(renderer, model, offscreen) {
		if (!renderer.isPremultipliedAlpha()) CubismLogError("NoPremultipliedAlpha is not allowed");
		if (this._shaderSets.length == 0) this.generateShaders();
		if (this._isShaderLoaded == false) {
			CubismLogWarning("Shader program is not initialized.");
			return;
		}
		let srcColor;
		let dstColor;
		let srcAlpha;
		let dstAlpha;
		const offscreenIndex = offscreen.getOffscreenIndex();
		const masked = renderer.getClippingContextBufferForOffscreen() != null;
		const invertedMask = model.getOffscreenInvertedMask(offscreenIndex);
		const offset = masked ? invertedMask ? 2 : 1 : 0;
		let shaderSet;
		let isUsingCompatible = true;
		const colorBlendMode = model.getOffscreenColorBlend(offscreenIndex);
		const alphaBlendMode = model.getOffscreenAlphaBlend(offscreenIndex);
		if (colorBlendMode == CubismColorBlend.ColorBlend_None || alphaBlendMode == CubismAlphaBlend.AlphaBlend_None || colorBlendMode == CubismColorBlend.ColorBlend_Normal && alphaBlendMode == CubismAlphaBlend.AlphaBlend_Over) {
			shaderSet = this._shaderSets[1 + offset];
			srcColor = this.gl.ONE;
			dstColor = this.gl.ONE_MINUS_SRC_ALPHA;
			srcAlpha = this.gl.ONE;
			dstAlpha = this.gl.ONE_MINUS_SRC_ALPHA;
		} else switch (colorBlendMode) {
			case CubismColorBlend.ColorBlend_AddCompatible:
				shaderSet = this._shaderSets[4 + offset];
				srcColor = this.gl.ONE;
				dstColor = this.gl.ONE;
				srcAlpha = this.gl.ZERO;
				dstAlpha = this.gl.ONE;
				break;
			case CubismColorBlend.ColorBlend_MultiplyCompatible:
				shaderSet = this._shaderSets[7 + offset];
				srcColor = this.gl.DST_COLOR;
				dstColor = this.gl.ONE_MINUS_SRC_ALPHA;
				srcAlpha = this.gl.ZERO;
				dstAlpha = this.gl.ONE;
				break;
			default: {
				const srcBuffer = offscreen.getOldOffscreen() != null ? offscreen.getOldOffscreen() : renderer.getModelRenderTarget(0);
				CubismRenderTarget_WebGL.copyBuffer(this.gl, srcBuffer, renderer.getModelRenderTarget(1));
				const baseShaderSetIndex = this._blendShaderSetMap.get(this._colorBlendMap.get(colorBlendMode) + this._alphaBlendMap.get(alphaBlendMode));
				shaderSet = this._shaderSets[baseShaderSetIndex + offset];
				srcColor = this.gl.ONE;
				dstColor = this.gl.ZERO;
				srcAlpha = this.gl.ONE;
				dstAlpha = this.gl.ZERO;
				isUsingCompatible = false;
			}
		}
		this.gl.useProgram(shaderSet.shaderProgram);
		CubismRenderTarget_WebGL.copyBuffer(this.gl, offscreen, renderer.getModelRenderTarget(2));
		this.gl.activeTexture(this.gl.TEXTURE0);
		const tex0 = renderer.getModelRenderTarget(2).getColorBuffer();
		this.gl.bindTexture(this.gl.TEXTURE_2D, tex0);
		this.gl.uniform1i(shaderSet.samplerTexture0Location, 0);
		const matrix4x4 = new CubismMatrix44();
		matrix4x4.loadIdentity();
		this.gl.uniformMatrix4fv(shaderSet.uniformMatrixLocation, false, matrix4x4.getArray());
		const offscreenOpacity = model.getOffscreenOpacity(offscreenIndex);
		const baseColor = new CubismTextureColor(offscreenOpacity, offscreenOpacity, offscreenOpacity, offscreenOpacity);
		const multiplyAndScreenColor = model.getOverrideMultiplyAndScreenColor();
		const multiplyColor = multiplyAndScreenColor.getOffscreenMultiplyColor(offscreenIndex);
		const screenColor = multiplyAndScreenColor.getOffscreenScreenColor(offscreenIndex);
		this.gl.uniform4f(shaderSet.uniformBaseColorLocation, baseColor.r, baseColor.g, baseColor.b, baseColor.a);
		this.gl.uniform4f(shaderSet.uniformMultiplyColorLocation, multiplyColor.r, multiplyColor.g, multiplyColor.b, multiplyColor.a);
		this.gl.uniform4f(shaderSet.uniformScreenColorLocation, screenColor.r, screenColor.g, screenColor.b, screenColor.a);
		this.gl.activeTexture(this.gl.TEXTURE2);
		if (!isUsingCompatible) {
			const tex1 = renderer.getModelRenderTarget(1).getColorBuffer();
			this.gl.bindTexture(this.gl.TEXTURE_2D, tex1);
			this.gl.uniform1i(shaderSet.samplerFrameBufferTextureLocation, 2);
		}
		if (masked) {
			this.gl.activeTexture(this.gl.TEXTURE1);
			const tex2 = renderer.getOffscreenMaskBuffer(renderer.getClippingContextBufferForOffscreen()._bufferIndex).getColorBuffer();
			this.gl.bindTexture(this.gl.TEXTURE_2D, tex2);
			this.gl.uniform1i(shaderSet.samplerTexture1Location, 1);
			this.gl.uniformMatrix4fv(shaderSet.uniformClipMatrixLocation, false, renderer.getClippingContextBufferForOffscreen()._matrixForDraw.getArray());
			const channelIndex = renderer.getClippingContextBufferForOffscreen()._layoutChannelIndex;
			const colorChannel = renderer.getClippingContextBufferForOffscreen().getClippingManager().getChannelFlagAsColor(channelIndex);
			this.gl.uniform4f(shaderSet.uniformChannelFlagLocation, colorChannel.r, colorChannel.g, colorChannel.b, colorChannel.a);
			if (model.isBlendModeEnabled()) this.gl.uniform1f(shaderSet.uniformInvertMaskFlagLocation, invertedMask ? 1 : 0);
		}
		if (!renderer._bufferData.vertex) renderer._bufferData.vertex = this.gl.createBuffer();
		this.gl.bindBuffer(this.gl.ARRAY_BUFFER, renderer._bufferData.vertex);
		this.gl.bufferData(this.gl.ARRAY_BUFFER, s_renderTargetVertexArray, this.gl.STATIC_DRAW);
		this.gl.enableVertexAttribArray(shaderSet.attributePositionLocation);
		this.gl.vertexAttribPointer(shaderSet.attributePositionLocation, 2, this.gl.FLOAT, false, Float32Array.BYTES_PER_ELEMENT * 2, 0);
		if (!renderer._bufferData.uv) renderer._bufferData.uv = this.gl.createBuffer();
		this.gl.bindBuffer(this.gl.ARRAY_BUFFER, renderer._bufferData.uv);
		this.gl.bufferData(this.gl.ARRAY_BUFFER, s_renderTargetReverseUvArray, this.gl.STATIC_DRAW);
		this.gl.enableVertexAttribArray(shaderSet.attributeTexCoordLocation);
		this.gl.vertexAttribPointer(shaderSet.attributeTexCoordLocation, 2, this.gl.FLOAT, false, Float32Array.BYTES_PER_ELEMENT * 2, 0);
		this.gl.blendFuncSeparate(srcColor, dstColor, srcAlpha, dstAlpha);
	}
	/**
	* マスク用のシェーダプログラムの一連のセットアップを実行する
	*
	* @param renderer レンダラー
	* @param model 描画対象のモデル
	* @param index 描画対象のメッシュのインデックス
	*/
	setupShaderProgramForMask(renderer, model, index) {
		if (!renderer.isPremultipliedAlpha()) CubismLogError("NoPremultipliedAlpha is not allowed");
		if (this._shaderSets.length == 0) this.generateShaders();
		if (this._isShaderLoaded == false) {
			CubismLogWarning("Shader program is not initialized.");
			return;
		}
		const shaderSet = this._shaderSets[0];
		this.gl.useProgram(shaderSet.shaderProgram);
		if (renderer._bufferData.vertex == null) renderer._bufferData.vertex = this.gl.createBuffer();
		this.gl.bindBuffer(this.gl.ARRAY_BUFFER, renderer._bufferData.vertex);
		const vertexArray = model.getDrawableVertices(index);
		this.gl.bufferData(this.gl.ARRAY_BUFFER, vertexArray, this.gl.DYNAMIC_DRAW);
		this.gl.enableVertexAttribArray(shaderSet.attributePositionLocation);
		this.gl.vertexAttribPointer(shaderSet.attributePositionLocation, 2, this.gl.FLOAT, false, 0, 0);
		if (renderer._bufferData.uv == null) renderer._bufferData.uv = this.gl.createBuffer();
		this.gl.bindBuffer(this.gl.ARRAY_BUFFER, renderer._bufferData.uv);
		const textureNo = model.getDrawableTextureIndex(index);
		const textureId = renderer.getBindedTextures().get(textureNo);
		this.gl.activeTexture(this.gl.TEXTURE0);
		this.gl.bindTexture(this.gl.TEXTURE_2D, textureId);
		this.gl.uniform1i(shaderSet.samplerTexture0Location, 0);
		if (renderer._bufferData.uv == null) renderer._bufferData.uv = this.gl.createBuffer();
		this.gl.bindBuffer(this.gl.ARRAY_BUFFER, renderer._bufferData.uv);
		const uvArray = model.getDrawableVertexUvs(index);
		this.gl.bufferData(this.gl.ARRAY_BUFFER, uvArray, this.gl.DYNAMIC_DRAW);
		this.gl.enableVertexAttribArray(shaderSet.attributeTexCoordLocation);
		this.gl.vertexAttribPointer(shaderSet.attributeTexCoordLocation, 2, this.gl.FLOAT, false, 0, 0);
		const channelIndex = renderer.getClippingContextBufferForMask()._layoutChannelIndex;
		const colorChannel = renderer.getClippingContextBufferForMask().getClippingManager().getChannelFlagAsColor(channelIndex);
		this.gl.uniform4f(shaderSet.uniformChannelFlagLocation, colorChannel.r, colorChannel.g, colorChannel.b, colorChannel.a);
		this.gl.uniformMatrix4fv(shaderSet.uniformClipMatrixLocation, false, renderer.getClippingContextBufferForMask()._matrixForMask.getArray());
		const rect = renderer.getClippingContextBufferForMask()._layoutBounds;
		this.gl.uniform4f(shaderSet.uniformBaseColorLocation, rect.x * 2 - 1, rect.y * 2 - 1, rect.getRight() * 2 - 1, rect.getBottom() * 2 - 1);
		const srcColor = this.gl.ZERO;
		const dstColor = this.gl.ONE_MINUS_SRC_COLOR;
		const srcAlpha = this.gl.ZERO;
		const dstAlpha = this.gl.ONE_MINUS_SRC_ALPHA;
		if (renderer._bufferData.index == null) renderer._bufferData.index = this.gl.createBuffer();
		const indexArray = model.getDrawableVertexIndices(index);
		this.gl.bindBuffer(this.gl.ELEMENT_ARRAY_BUFFER, renderer._bufferData.index);
		this.gl.bufferData(this.gl.ELEMENT_ARRAY_BUFFER, indexArray, this.gl.DYNAMIC_DRAW);
		this.gl.blendFuncSeparate(srcColor, dstColor, srcAlpha, dstAlpha);
	}
	/**
	* オフスクリーンのレンダリングターゲット用のシェーダープログラムを設定する
	*
	* @param renderer レンダラー
	*/
	setupShaderProgramForOffscreenRenderTarget(renderer) {
		if (this._shaderSets.length == 0) this.generateShaders();
		if (this._isShaderLoaded == false) {
			CubismLogWarning("Shader program is not initialized.");
			return;
		}
		const baseColor = renderer.getModelColor();
		baseColor.r *= baseColor.a;
		baseColor.g *= baseColor.a;
		baseColor.b *= baseColor.a;
		this.copyTexture(renderer, baseColor);
	}
	/**
	* オフスクリーンのレンダリングターゲットの内容をコピーする
	*
	* @param renderer レンダラー
	* @param baseColor ベースカラー
	*/
	copyTexture(renderer, baseColor) {
		const srcColor = this.gl.ONE;
		const dstColor = this.gl.ONE_MINUS_SRC_ALPHA;
		const srcAlpha = this.gl.ONE;
		const dstAlpha = this.gl.ONE_MINUS_SRC_ALPHA;
		const shaderSet = this._shaderSets[10];
		this.gl.useProgram(shaderSet.shaderProgram);
		this.gl.uniform4f(shaderSet.uniformBaseColorLocation, baseColor.r, baseColor.g, baseColor.b, baseColor.a);
		this.gl.activeTexture(this.gl.TEXTURE0);
		const tex = renderer.getModelRenderTarget(0).getColorBuffer();
		this.gl.bindTexture(this.gl.TEXTURE_2D, tex);
		this.gl.uniform1i(shaderSet.samplerTexture0Location, 0);
		if (!renderer._bufferData.vertex) renderer._bufferData.vertex = this.gl.createBuffer();
		this.gl.bindBuffer(this.gl.ARRAY_BUFFER, renderer._bufferData.vertex);
		this.gl.bufferData(this.gl.ARRAY_BUFFER, s_renderTargetVertexArray, this.gl.STATIC_DRAW);
		this.gl.enableVertexAttribArray(shaderSet.attributePositionLocation);
		this.gl.vertexAttribPointer(shaderSet.attributePositionLocation, 2, this.gl.FLOAT, false, Float32Array.BYTES_PER_ELEMENT * 2, 0);
		if (!renderer._bufferData.uv) renderer._bufferData.uv = this.gl.createBuffer();
		this.gl.bindBuffer(this.gl.ARRAY_BUFFER, renderer._bufferData.uv);
		this.gl.bufferData(this.gl.ARRAY_BUFFER, s_renderTargetUvArray, this.gl.STATIC_DRAW);
		this.gl.enableVertexAttribArray(shaderSet.attributeTexCoordLocation);
		this.gl.vertexAttribPointer(shaderSet.attributeTexCoordLocation, 2, this.gl.FLOAT, false, Float32Array.BYTES_PER_ELEMENT * 2, 0);
		this.gl.blendFuncSeparate(srcColor, dstColor, srcAlpha, dstAlpha);
	}
	/**
	* シェーダープログラムを解放する
	*/
	releaseShaderProgram() {
		for (let i = 0; i < this._shaderSets.length; i++) {
			this.gl.deleteProgram(this._shaderSets[i].shaderProgram);
			this._shaderSets[i].shaderProgram = 0;
			this._shaderSets[i] = void 0;
			this._shaderSets[i] = null;
		}
	}
	/**
	* シェーダープログラムを初期化する
	*
	* @param vertShaderSrc 頂点シェーダのソース
	* @param fragShaderSrc フラグメントシェーダのソース
	*/
	generateShaders() {
		if (this._isShaderLoading) return;
		this._isShaderLoading = true;
		this._isShaderLoaded = false;
		this._shaderSets.length = this._shaderCount;
		for (let i = 0; i < this._shaderCount; i++) this._shaderSets[i] = new CubismShaderSet();
		this.loadShaders().then(() => {
			this.registerShader();
			this.registerBlendShader();
			this._isShaderLoading = false;
			this._isShaderLoaded = true;
		}).catch((error) => {
			this._isShaderLoading = false;
			console.error("Failed to load shaders:", error);
		});
	}
	/**
	* シェーダープログラムを登録する
	*/
	registerShader() {
		const vertexShaderSrc = this._vertShaderSrc;
		const vertexShaderSrcMasked = this._vertShaderSrcMasked;
		const vertexShaderSrcSetupMask = this._vertShaderSrcSetupMask;
		const fragmentShaderSrcSetupMask = this._fragShaderSrcSetupMask;
		const fragmentShaderSrcPremultipliedAlpha = this._fragShaderSrcPremultipliedAlpha;
		const fragmentShaderSrcMaskPremultipliedAlpha = this._fragShaderSrcMaskPremultipliedAlpha;
		const fragmentShaderSrcMaskInvertedPremultipliedAlpha = this._fragShaderSrcMaskInvertedPremultipliedAlpha;
		this._shaderSets[0].shaderProgram = this.loadShaderProgram(vertexShaderSrcSetupMask, fragmentShaderSrcSetupMask);
		this._shaderSets[1].shaderProgram = this.loadShaderProgram(vertexShaderSrc, fragmentShaderSrcPremultipliedAlpha);
		this._shaderSets[2].shaderProgram = this.loadShaderProgram(vertexShaderSrcMasked, fragmentShaderSrcMaskPremultipliedAlpha);
		this._shaderSets[3].shaderProgram = this.loadShaderProgram(vertexShaderSrcMasked, fragmentShaderSrcMaskInvertedPremultipliedAlpha);
		this._shaderSets[4].shaderProgram = this._shaderSets[1].shaderProgram;
		this._shaderSets[5].shaderProgram = this._shaderSets[2].shaderProgram;
		this._shaderSets[6].shaderProgram = this._shaderSets[3].shaderProgram;
		this._shaderSets[7].shaderProgram = this._shaderSets[1].shaderProgram;
		this._shaderSets[8].shaderProgram = this._shaderSets[2].shaderProgram;
		this._shaderSets[9].shaderProgram = this._shaderSets[3].shaderProgram;
		this._shaderSets[0].attributePositionLocation = this.gl.getAttribLocation(this._shaderSets[0].shaderProgram, "a_position");
		this._shaderSets[0].attributeTexCoordLocation = this.gl.getAttribLocation(this._shaderSets[0].shaderProgram, "a_texCoord");
		this._shaderSets[0].samplerTexture0Location = this.gl.getUniformLocation(this._shaderSets[0].shaderProgram, "s_texture0");
		this._shaderSets[0].uniformClipMatrixLocation = this.gl.getUniformLocation(this._shaderSets[0].shaderProgram, "u_clipMatrix");
		this._shaderSets[0].uniformChannelFlagLocation = this.gl.getUniformLocation(this._shaderSets[0].shaderProgram, "u_channelFlag");
		this._shaderSets[0].uniformBaseColorLocation = this.gl.getUniformLocation(this._shaderSets[0].shaderProgram, "u_baseColor");
		this._shaderSets[1].attributePositionLocation = this.gl.getAttribLocation(this._shaderSets[1].shaderProgram, "a_position");
		this._shaderSets[1].attributeTexCoordLocation = this.gl.getAttribLocation(this._shaderSets[1].shaderProgram, "a_texCoord");
		this._shaderSets[1].samplerTexture0Location = this.gl.getUniformLocation(this._shaderSets[1].shaderProgram, "s_texture0");
		this._shaderSets[1].uniformMatrixLocation = this.gl.getUniformLocation(this._shaderSets[1].shaderProgram, "u_matrix");
		this._shaderSets[1].uniformBaseColorLocation = this.gl.getUniformLocation(this._shaderSets[1].shaderProgram, "u_baseColor");
		this._shaderSets[1].uniformMultiplyColorLocation = this.gl.getUniformLocation(this._shaderSets[1].shaderProgram, "u_multiplyColor");
		this._shaderSets[1].uniformScreenColorLocation = this.gl.getUniformLocation(this._shaderSets[1].shaderProgram, "u_screenColor");
		this._shaderSets[2].attributePositionLocation = this.gl.getAttribLocation(this._shaderSets[2].shaderProgram, "a_position");
		this._shaderSets[2].attributeTexCoordLocation = this.gl.getAttribLocation(this._shaderSets[2].shaderProgram, "a_texCoord");
		this._shaderSets[2].samplerTexture0Location = this.gl.getUniformLocation(this._shaderSets[2].shaderProgram, "s_texture0");
		this._shaderSets[2].samplerTexture1Location = this.gl.getUniformLocation(this._shaderSets[2].shaderProgram, "s_texture1");
		this._shaderSets[2].uniformMatrixLocation = this.gl.getUniformLocation(this._shaderSets[2].shaderProgram, "u_matrix");
		this._shaderSets[2].uniformClipMatrixLocation = this.gl.getUniformLocation(this._shaderSets[2].shaderProgram, "u_clipMatrix");
		this._shaderSets[2].uniformChannelFlagLocation = this.gl.getUniformLocation(this._shaderSets[2].shaderProgram, "u_channelFlag");
		this._shaderSets[2].uniformBaseColorLocation = this.gl.getUniformLocation(this._shaderSets[2].shaderProgram, "u_baseColor");
		this._shaderSets[2].uniformMultiplyColorLocation = this.gl.getUniformLocation(this._shaderSets[2].shaderProgram, "u_multiplyColor");
		this._shaderSets[2].uniformScreenColorLocation = this.gl.getUniformLocation(this._shaderSets[2].shaderProgram, "u_screenColor");
		this._shaderSets[3].attributePositionLocation = this.gl.getAttribLocation(this._shaderSets[3].shaderProgram, "a_position");
		this._shaderSets[3].attributeTexCoordLocation = this.gl.getAttribLocation(this._shaderSets[3].shaderProgram, "a_texCoord");
		this._shaderSets[3].samplerTexture0Location = this.gl.getUniformLocation(this._shaderSets[3].shaderProgram, "s_texture0");
		this._shaderSets[3].samplerTexture1Location = this.gl.getUniformLocation(this._shaderSets[3].shaderProgram, "s_texture1");
		this._shaderSets[3].uniformMatrixLocation = this.gl.getUniformLocation(this._shaderSets[3].shaderProgram, "u_matrix");
		this._shaderSets[3].uniformClipMatrixLocation = this.gl.getUniformLocation(this._shaderSets[3].shaderProgram, "u_clipMatrix");
		this._shaderSets[3].uniformChannelFlagLocation = this.gl.getUniformLocation(this._shaderSets[3].shaderProgram, "u_channelFlag");
		this._shaderSets[3].uniformBaseColorLocation = this.gl.getUniformLocation(this._shaderSets[3].shaderProgram, "u_baseColor");
		this._shaderSets[3].uniformMultiplyColorLocation = this.gl.getUniformLocation(this._shaderSets[3].shaderProgram, "u_multiplyColor");
		this._shaderSets[3].uniformScreenColorLocation = this.gl.getUniformLocation(this._shaderSets[3].shaderProgram, "u_screenColor");
		this._shaderSets[4].attributePositionLocation = this.gl.getAttribLocation(this._shaderSets[4].shaderProgram, "a_position");
		this._shaderSets[4].attributeTexCoordLocation = this.gl.getAttribLocation(this._shaderSets[4].shaderProgram, "a_texCoord");
		this._shaderSets[4].samplerTexture0Location = this.gl.getUniformLocation(this._shaderSets[4].shaderProgram, "s_texture0");
		this._shaderSets[4].uniformMatrixLocation = this.gl.getUniformLocation(this._shaderSets[4].shaderProgram, "u_matrix");
		this._shaderSets[4].uniformBaseColorLocation = this.gl.getUniformLocation(this._shaderSets[4].shaderProgram, "u_baseColor");
		this._shaderSets[4].uniformMultiplyColorLocation = this.gl.getUniformLocation(this._shaderSets[4].shaderProgram, "u_multiplyColor");
		this._shaderSets[4].uniformScreenColorLocation = this.gl.getUniformLocation(this._shaderSets[4].shaderProgram, "u_screenColor");
		this._shaderSets[5].attributePositionLocation = this.gl.getAttribLocation(this._shaderSets[5].shaderProgram, "a_position");
		this._shaderSets[5].attributeTexCoordLocation = this.gl.getAttribLocation(this._shaderSets[5].shaderProgram, "a_texCoord");
		this._shaderSets[5].samplerTexture0Location = this.gl.getUniformLocation(this._shaderSets[5].shaderProgram, "s_texture0");
		this._shaderSets[5].samplerTexture1Location = this.gl.getUniformLocation(this._shaderSets[5].shaderProgram, "s_texture1");
		this._shaderSets[5].uniformMatrixLocation = this.gl.getUniformLocation(this._shaderSets[5].shaderProgram, "u_matrix");
		this._shaderSets[5].uniformClipMatrixLocation = this.gl.getUniformLocation(this._shaderSets[5].shaderProgram, "u_clipMatrix");
		this._shaderSets[5].uniformChannelFlagLocation = this.gl.getUniformLocation(this._shaderSets[5].shaderProgram, "u_channelFlag");
		this._shaderSets[5].uniformBaseColorLocation = this.gl.getUniformLocation(this._shaderSets[5].shaderProgram, "u_baseColor");
		this._shaderSets[5].uniformMultiplyColorLocation = this.gl.getUniformLocation(this._shaderSets[5].shaderProgram, "u_multiplyColor");
		this._shaderSets[5].uniformScreenColorLocation = this.gl.getUniformLocation(this._shaderSets[5].shaderProgram, "u_screenColor");
		this._shaderSets[6].attributePositionLocation = this.gl.getAttribLocation(this._shaderSets[6].shaderProgram, "a_position");
		this._shaderSets[6].attributeTexCoordLocation = this.gl.getAttribLocation(this._shaderSets[6].shaderProgram, "a_texCoord");
		this._shaderSets[6].samplerTexture0Location = this.gl.getUniformLocation(this._shaderSets[6].shaderProgram, "s_texture0");
		this._shaderSets[6].samplerTexture1Location = this.gl.getUniformLocation(this._shaderSets[6].shaderProgram, "s_texture1");
		this._shaderSets[6].uniformMatrixLocation = this.gl.getUniformLocation(this._shaderSets[6].shaderProgram, "u_matrix");
		this._shaderSets[6].uniformClipMatrixLocation = this.gl.getUniformLocation(this._shaderSets[6].shaderProgram, "u_clipMatrix");
		this._shaderSets[6].uniformChannelFlagLocation = this.gl.getUniformLocation(this._shaderSets[6].shaderProgram, "u_channelFlag");
		this._shaderSets[6].uniformBaseColorLocation = this.gl.getUniformLocation(this._shaderSets[6].shaderProgram, "u_baseColor");
		this._shaderSets[6].uniformMultiplyColorLocation = this.gl.getUniformLocation(this._shaderSets[6].shaderProgram, "u_multiplyColor");
		this._shaderSets[6].uniformScreenColorLocation = this.gl.getUniformLocation(this._shaderSets[6].shaderProgram, "u_screenColor");
		this._shaderSets[7].attributePositionLocation = this.gl.getAttribLocation(this._shaderSets[7].shaderProgram, "a_position");
		this._shaderSets[7].attributeTexCoordLocation = this.gl.getAttribLocation(this._shaderSets[7].shaderProgram, "a_texCoord");
		this._shaderSets[7].samplerTexture0Location = this.gl.getUniformLocation(this._shaderSets[7].shaderProgram, "s_texture0");
		this._shaderSets[7].uniformMatrixLocation = this.gl.getUniformLocation(this._shaderSets[7].shaderProgram, "u_matrix");
		this._shaderSets[7].uniformBaseColorLocation = this.gl.getUniformLocation(this._shaderSets[7].shaderProgram, "u_baseColor");
		this._shaderSets[7].uniformMultiplyColorLocation = this.gl.getUniformLocation(this._shaderSets[7].shaderProgram, "u_multiplyColor");
		this._shaderSets[7].uniformScreenColorLocation = this.gl.getUniformLocation(this._shaderSets[7].shaderProgram, "u_screenColor");
		this._shaderSets[8].attributePositionLocation = this.gl.getAttribLocation(this._shaderSets[8].shaderProgram, "a_position");
		this._shaderSets[8].attributeTexCoordLocation = this.gl.getAttribLocation(this._shaderSets[8].shaderProgram, "a_texCoord");
		this._shaderSets[8].samplerTexture0Location = this.gl.getUniformLocation(this._shaderSets[8].shaderProgram, "s_texture0");
		this._shaderSets[8].samplerTexture1Location = this.gl.getUniformLocation(this._shaderSets[8].shaderProgram, "s_texture1");
		this._shaderSets[8].uniformMatrixLocation = this.gl.getUniformLocation(this._shaderSets[8].shaderProgram, "u_matrix");
		this._shaderSets[8].uniformClipMatrixLocation = this.gl.getUniformLocation(this._shaderSets[8].shaderProgram, "u_clipMatrix");
		this._shaderSets[8].uniformChannelFlagLocation = this.gl.getUniformLocation(this._shaderSets[8].shaderProgram, "u_channelFlag");
		this._shaderSets[8].uniformBaseColorLocation = this.gl.getUniformLocation(this._shaderSets[8].shaderProgram, "u_baseColor");
		this._shaderSets[8].uniformMultiplyColorLocation = this.gl.getUniformLocation(this._shaderSets[8].shaderProgram, "u_multiplyColor");
		this._shaderSets[8].uniformScreenColorLocation = this.gl.getUniformLocation(this._shaderSets[8].shaderProgram, "u_screenColor");
		this._shaderSets[9].attributePositionLocation = this.gl.getAttribLocation(this._shaderSets[9].shaderProgram, "a_position");
		this._shaderSets[9].attributeTexCoordLocation = this.gl.getAttribLocation(this._shaderSets[9].shaderProgram, "a_texCoord");
		this._shaderSets[9].samplerTexture0Location = this.gl.getUniformLocation(this._shaderSets[9].shaderProgram, "s_texture0");
		this._shaderSets[9].samplerTexture1Location = this.gl.getUniformLocation(this._shaderSets[9].shaderProgram, "s_texture1");
		this._shaderSets[9].uniformMatrixLocation = this.gl.getUniformLocation(this._shaderSets[9].shaderProgram, "u_matrix");
		this._shaderSets[9].uniformClipMatrixLocation = this.gl.getUniformLocation(this._shaderSets[9].shaderProgram, "u_clipMatrix");
		this._shaderSets[9].uniformChannelFlagLocation = this.gl.getUniformLocation(this._shaderSets[9].shaderProgram, "u_channelFlag");
		this._shaderSets[9].uniformBaseColorLocation = this.gl.getUniformLocation(this._shaderSets[9].shaderProgram, "u_baseColor");
		this._shaderSets[9].uniformMultiplyColorLocation = this.gl.getUniformLocation(this._shaderSets[9].shaderProgram, "u_multiplyColor");
		this._shaderSets[9].uniformScreenColorLocation = this.gl.getUniformLocation(this._shaderSets[9].shaderProgram, "u_screenColor");
	}
	/**
	* ブレンドモード用のシェーダープログラムを登録する
	*/
	registerBlendShader() {
		const vertShaderSrcCopy = this._vertShaderSrcCopy;
		const fragShaderSrcCopy = this._fragShaderSrcCopy;
		const copyShaderSet = this._shaderSets[10];
		copyShaderSet.shaderProgram = this.loadShaderProgram(vertShaderSrcCopy, fragShaderSrcCopy);
		copyShaderSet.attributeTexCoordLocation = this.gl.getAttribLocation(copyShaderSet.shaderProgram, "a_texCoord");
		copyShaderSet.attributePositionLocation = this.gl.getAttribLocation(copyShaderSet.shaderProgram, "a_position");
		copyShaderSet.uniformBaseColorLocation = this.gl.getUniformLocation(copyShaderSet.shaderProgram, "u_baseColor");
		let shaderSetIndex = 11;
		for (let colorBlendIndex = 0; colorBlendIndex < this._colorBlendValues.length; colorBlendIndex++) {
			if (this._colorBlendValues[colorBlendIndex] == CubismColorBlend.ColorBlend_None || this._colorBlendValues[colorBlendIndex] == CubismColorBlend.ColorBlend_AddCompatible || this._colorBlendValues[colorBlendIndex] == CubismColorBlend.ColorBlend_MultiplyCompatible) continue;
			const colorBlendValue = this._colorBlendValues[colorBlendIndex];
			const colorBlendMacro = `#define COLOR_BLEND_${this._colorBlendMap.get(colorBlendValue).toUpperCase()}\n`;
			for (let alphablendIndex = 0; alphablendIndex < this._alphaBlendValues.length; alphablendIndex++) {
				if (this._alphaBlendValues[alphablendIndex] == CubismAlphaBlend.AlphaBlend_None || this._colorBlendValues[colorBlendIndex] == CubismColorBlend.ColorBlend_Normal && this._alphaBlendValues[alphablendIndex] == CubismAlphaBlend.AlphaBlend_Over) continue;
				const alphaBlendValue = this._alphaBlendValues[alphablendIndex];
				const alphaBlendMacro = `#define ALPHA_BLEND_${this._alphaBlendMap.get(alphaBlendValue).toUpperCase()}\n`;
				this.generateBlendShader(colorBlendMacro, alphaBlendMacro, shaderSetIndex);
				this._blendShaderSetMap.set(this._colorBlendMap.get(this._colorBlendValues[colorBlendIndex]) + this._alphaBlendMap.get(this._alphaBlendValues[alphablendIndex]), shaderSetIndex);
				shaderSetIndex += 3;
			}
		}
	}
	/**
	* ブレンドモード用のシェーダープログラムを生成する
	*
	* @param colorBlendMacro カラーブレンド用のマクロ
	* @param alphaBlendMacro アルファブレンド用のマクロ
	* @param shaderSetBaseIndex _shaderSets のインデックス
	*/
	generateBlendShader(colorBlendMacro, alphaBlendMacro, shaderSetBaseIndex) {
		for (let shaderTypeIndex = 0; shaderTypeIndex < 3; shaderTypeIndex++) {
			let vertexShaderSrc = "";
			let fragmentShaderStr = "precision mediump float;\n";
			const shaderSetIndex = shaderSetBaseIndex + shaderTypeIndex;
			fragmentShaderStr += colorBlendMacro;
			fragmentShaderStr += alphaBlendMacro;
			fragmentShaderStr += this._fragShaderSrcColorBlend;
			fragmentShaderStr += this._fragShaderSrcAlphaBlend;
			if (shaderTypeIndex == 1 || shaderTypeIndex == 2) {
				const clippingMaskMacro = "#define CLIPPING_MASK\n";
				vertexShaderSrc += clippingMaskMacro;
				fragmentShaderStr += clippingMaskMacro;
			}
			vertexShaderSrc += this._vertShaderSrcBlend;
			fragmentShaderStr += this._fragShaderSrcBlend;
			this._shaderSets[shaderSetIndex].shaderProgram = this.loadShaderProgram(vertexShaderSrc, fragmentShaderStr);
			this._shaderSets[shaderSetIndex].attributePositionLocation = this.gl.getAttribLocation(this._shaderSets[shaderSetIndex].shaderProgram, "a_position");
			this._shaderSets[shaderSetIndex].attributeTexCoordLocation = this.gl.getAttribLocation(this._shaderSets[shaderSetIndex].shaderProgram, "a_texCoord");
			this._shaderSets[shaderSetIndex].samplerTexture0Location = this.gl.getUniformLocation(this._shaderSets[shaderSetIndex].shaderProgram, "s_texture0");
			this._shaderSets[shaderSetIndex].uniformMatrixLocation = this.gl.getUniformLocation(this._shaderSets[shaderSetIndex].shaderProgram, "u_matrix");
			this._shaderSets[shaderSetIndex].uniformBaseColorLocation = this.gl.getUniformLocation(this._shaderSets[shaderSetIndex].shaderProgram, "u_baseColor");
			this._shaderSets[shaderSetIndex].uniformMultiplyColorLocation = this.gl.getUniformLocation(this._shaderSets[shaderSetIndex].shaderProgram, "u_multiplyColor");
			this._shaderSets[shaderSetIndex].uniformScreenColorLocation = this.gl.getUniformLocation(this._shaderSets[shaderSetIndex].shaderProgram, "u_screenColor");
			this._shaderSets[shaderSetIndex].samplerFrameBufferTextureLocation = this.gl.getUniformLocation(this._shaderSets[shaderSetIndex].shaderProgram, "s_blendTexture");
			if (shaderTypeIndex == 1 || shaderTypeIndex == 2) {
				this._shaderSets[shaderSetIndex].samplerTexture1Location = this.gl.getUniformLocation(this._shaderSets[shaderSetIndex].shaderProgram, "s_texture1");
				this._shaderSets[shaderSetIndex].uniformClipMatrixLocation = this.gl.getUniformLocation(this._shaderSets[shaderSetIndex].shaderProgram, "u_clipMatrix");
				this._shaderSets[shaderSetIndex].uniformChannelFlagLocation = this.gl.getUniformLocation(this._shaderSets[shaderSetIndex].shaderProgram, "u_channelFlag");
				this._shaderSets[shaderSetIndex].uniformInvertMaskFlagLocation = this.gl.getUniformLocation(this._shaderSets[shaderSetIndex].shaderProgram, "u_invertClippingMask");
			}
		}
	}
	/**
	* シェーダプログラムをロードしてアドレスを返す
	*
	* @param vertexShaderSource    頂点シェーダのソース
	* @param fragmentShaderSource  フラグメントシェーダのソース
	*
	* @return シェーダプログラムのアドレス
	*/
	loadShaderProgram(vertexShaderSource, fragmentShaderSource) {
		let shaderProgram = this.gl.createProgram();
		let vertShader = this.compileShaderSource(this.gl.VERTEX_SHADER, vertexShaderSource);
		if (!vertShader) {
			CubismLogError("Vertex shader compile error!");
			return 0;
		}
		let fragShader = this.compileShaderSource(this.gl.FRAGMENT_SHADER, fragmentShaderSource);
		if (!fragShader) {
			CubismLogError("Fragment shader compile error!");
			return 0;
		}
		this.gl.attachShader(shaderProgram, vertShader);
		this.gl.attachShader(shaderProgram, fragShader);
		this.gl.linkProgram(shaderProgram);
		if (!this.gl.getProgramParameter(shaderProgram, this.gl.LINK_STATUS)) {
			CubismLogError("Failed to link program: {0}", shaderProgram);
			this.gl.deleteShader(vertShader);
			vertShader = 0;
			this.gl.deleteShader(fragShader);
			fragShader = 0;
			if (shaderProgram) {
				this.gl.deleteProgram(shaderProgram);
				shaderProgram = 0;
			}
			return 0;
		}
		this.gl.deleteShader(vertShader);
		this.gl.deleteShader(fragShader);
		return shaderProgram;
	}
	/**
	* シェーダープログラムをコンパイルする
	*
	* @param shaderType シェーダタイプ(Vertex/Fragment)
	* @param shaderSource シェーダソースコード
	*
	* @return コンパイルされたシェーダープログラム
	*/
	compileShaderSource(shaderType, shaderSource) {
		const source = shaderSource;
		const shader = this.gl.createShader(shaderType);
		this.gl.shaderSource(shader, source);
		this.gl.compileShader(shader);
		if (!shader) {
			const log = this.gl.getShaderInfoLog(shader);
			CubismLogError("Shader compile log: {0} ", log);
		}
		if (!this.gl.getShaderParameter(shader, this.gl.COMPILE_STATUS)) {
			const log = this.gl.getShaderInfoLog(shader);
			CubismLogError("Shader compile log: {0} ", log);
			this.gl.deleteShader(shader);
			return null;
		}
		return shader;
	}
	/**
	* WebGLレンダリングコンテキストを設定する
	*
	* @param gl WebGLレンダリングコンテキスト
	*/
	setGl(gl) {
		this.gl = gl;
	}
	/**
	* ブレンドモード用のシェーダーパスを設定する
	*
	* @param shaderPath シェーダーパス
	*/
	setShaderPath(shaderPath) {
		this._shaderPath = shaderPath;
	}
	/**
	* シェーダーパスを取得する
	*
	* @return シェーダーパス
	*/
	getShaderPath() {
		return this._shaderPath;
	}
};
/**
* GLContextごとにCubismShader_WebGLを確保するためのクラス
* シングルトンなクラスであり、CubismShaderManager_WebGL.getInstanceからアクセスする。
*/
var CubismShaderManager_WebGL = class CubismShaderManager_WebGL {
	/**
	* インスタンスを取得する（シングルトン）
	*
	* @return インスタンス
	*/
	static getInstance() {
		if (s_instance == null) s_instance = new CubismShaderManager_WebGL();
		return s_instance;
	}
	/**
	* インスタンスを開放する（シングルトン）
	*/
	static deleteInstance() {
		if (s_instance) {
			s_instance.release();
			s_instance = null;
		}
	}
	/**
	* Privateなコンストラクタ
	*/
	constructor() {
		this._shaderMap = /* @__PURE__ */ new Map();
	}
	/**
	* デストラクタ相当の処理
	*/
	release() {
		for (const item of this._shaderMap) item[1].release();
		this._shaderMap.clear();
	}
	/**
	* GLContextをキーにShaderを取得する
	*
	* @param gl glコンテキスト
	*
	* @return shaderを返す
	*/
	getShader(gl) {
		return this._shaderMap.get(gl);
	}
	/**
	* GLContextを登録する
	*
	* @param gl glコンテキスト
	*/
	setGlContext(gl) {
		if (!this._shaderMap.has(gl)) {
			const instance = new CubismShader_WebGL();
			instance.setGl(gl);
			this._shaderMap.set(gl, instance);
		}
	}
};
/**
* CubismShader_WebGLのインナークラス
*/
var CubismShaderSet = class {};
/**
* シェーダーの名前を定義する列挙型
*/
var ShaderNames = /* @__PURE__ */ function(ShaderNames) {
	ShaderNames[ShaderNames["ShaderNames_SetupMask"] = 0] = "ShaderNames_SetupMask";
	ShaderNames[ShaderNames["ShaderNames_NormalPremultipliedAlpha"] = 1] = "ShaderNames_NormalPremultipliedAlpha";
	ShaderNames[ShaderNames["ShaderNames_NormalMaskedPremultipliedAlpha"] = 2] = "ShaderNames_NormalMaskedPremultipliedAlpha";
	ShaderNames[ShaderNames["ShaderNames_NomralMaskedInvertedPremultipliedAlpha"] = 3] = "ShaderNames_NomralMaskedInvertedPremultipliedAlpha";
	ShaderNames[ShaderNames["ShaderNames_AddPremultipliedAlpha"] = 4] = "ShaderNames_AddPremultipliedAlpha";
	ShaderNames[ShaderNames["ShaderNames_AddMaskedPremultipliedAlpha"] = 5] = "ShaderNames_AddMaskedPremultipliedAlpha";
	ShaderNames[ShaderNames["ShaderNames_AddMaskedPremultipliedAlphaInverted"] = 6] = "ShaderNames_AddMaskedPremultipliedAlphaInverted";
	ShaderNames[ShaderNames["ShaderNames_MultPremultipliedAlpha"] = 7] = "ShaderNames_MultPremultipliedAlpha";
	ShaderNames[ShaderNames["ShaderNames_MultMaskedPremultipliedAlpha"] = 8] = "ShaderNames_MultMaskedPremultipliedAlpha";
	ShaderNames[ShaderNames["ShaderNames_MultMaskedPremultipliedAlphaInverted"] = 9] = "ShaderNames_MultMaskedPremultipliedAlphaInverted";
	ShaderNames[ShaderNames["ShaderNames_ShaderCount"] = 10] = "ShaderNames_ShaderCount";
	return ShaderNames;
}({});
var Live2DCubismFramework$5;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismShaderSet = CubismShaderSet;
	_Live2DCubismFramework.CubismShader_WebGL = CubismShader_WebGL;
	_Live2DCubismFramework.CubismShaderManager_WebGL = CubismShaderManager_WebGL;
	_Live2DCubismFramework.ShaderNames = ShaderNames;
})(Live2DCubismFramework$5 || (Live2DCubismFramework$5 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/rendering/cubismoffscreenmanager.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
/**
* フレームバッファなどのコンテナのクラス
*/
var CubismRenderTargetContainer = class {
	/**
	* Constructor
	*
	* @param colorBuffer カラーバッファ
	* @param renderTexture レンダーテクスチャ
	* @param inUse 使用中かどうか
	*/
	constructor(colorBuffer = null, renderTexture = null, inUse = false) {
		this.colorBuffer = colorBuffer;
		this.renderTexture = renderTexture;
		this.inUse = inUse;
	}
	clear() {
		this.colorBuffer = null;
		this.renderTexture = null;
		this.inUse = false;
	}
	/**
	* カラーバッファを取得
	*
	* @returns カラーバッファ
	*/
	getColorBuffer() {
		return this.colorBuffer;
	}
	/**
	* レンダーテクスチャを取得
	*
	* @returns レンダーテクスチャ
	*/
	getRenderTexture() {
		return this.renderTexture;
	}
};
/**
* WebGLContextごとのリソース管理を行う内部クラス
*/
var CubismWebGLContextManager = class {
	constructor(gl) {
		this.gl = gl;
		this.offscreenRenderTargetContainers = new Array();
		this.previousActiveRenderTextureMaxCount = 0;
		this.currentActiveRenderTextureCount = 0;
		this.hasResetThisFrame = false;
		this.width = 0;
		this.height = 0;
	}
	release() {
		if (this.offscreenRenderTargetContainers != null) {
			for (let index = 0; index < this.offscreenRenderTargetContainers.length; ++index) {
				const container = this.offscreenRenderTargetContainers[index];
				this.gl.deleteTexture(container.colorBuffer);
				this.gl.deleteFramebuffer(container.renderTexture);
			}
			this.offscreenRenderTargetContainers.length = 0;
			this.offscreenRenderTargetContainers = null;
		}
	}
};
/**
* WebGL用オフスクリーン描画機能を管理するマネージャ
* オフスクリーン描画機能に必要なフレームバッファなどを含むコンテナを管理する。
* 複数のWebGLContextに対応。
*/
var CubismWebGLOffscreenManager = class CubismWebGLOffscreenManager {
	/**
	* コンストラクタ
	*/
	constructor() {
		this._contextManagers = /* @__PURE__ */ new Map();
	}
	/**
	* デストラクタ相当の処理
	*/
	release() {
		if (this._contextManagers != null) {
			for (const manager of this._contextManagers.values()) manager.release();
			this._contextManagers.clear();
			this._contextManagers = null;
		}
		CubismWebGLOffscreenManager._instance = null;
	}
	/**
	* インスタンスの取得
	*
	* @return インスタンス
	*/
	static getInstance() {
		if (this._instance == null) this._instance = new CubismWebGLOffscreenManager();
		return this._instance;
	}
	/**
	* WebGLContextに対応するマネージャーを取得または作成
	*
	* @param gl WebGLRenderingContextまたはWebGL2RenderingContext
	* @return WebGLContextManager
	*/
	getContextManager(gl) {
		if (!this._contextManagers.has(gl)) this._contextManagers.set(gl, new CubismWebGLContextManager(gl));
		return this._contextManagers.get(gl);
	}
	/**
	* 指定されたWebGLContextのマネージャーを削除
	*
	* @param gl WebGLRenderingContextまたはWebGL2RenderingContext
	*/
	removeContext(gl) {
		if (this._contextManagers.has(gl)) {
			this._contextManagers.get(gl).release();
			this._contextManagers.delete(gl);
		}
	}
	/**
	* 初期化処理
	*
	* @param gl WebGLRenderingContextまたはWebGL2RenderingContext
	* @param width 幅
	* @param height 高さ
	*/
	initialize(gl, width, height) {
		const contextManager = this.getContextManager(gl);
		if (contextManager.offscreenRenderTargetContainers != null) {
			for (let index = 0; index < contextManager.offscreenRenderTargetContainers.length; ++index) {
				const container = contextManager.offscreenRenderTargetContainers[index];
				contextManager.gl.deleteTexture(container.colorBuffer);
				contextManager.gl.deleteFramebuffer(container.renderTexture);
				container.clear();
			}
			contextManager.offscreenRenderTargetContainers.length = 0;
		} else contextManager.offscreenRenderTargetContainers = new Array();
		contextManager.width = width;
		contextManager.height = height;
		contextManager.previousActiveRenderTextureMaxCount = 0;
		contextManager.currentActiveRenderTextureCount = 0;
		contextManager.hasResetThisFrame = false;
	}
	/**
	* モデルを描画する前に呼び出すフレーム開始時の処理を行う
	*
	* @param gl WebGLRenderingContextまたはWebGL2RenderingContext
	*/
	beginFrameProcess(gl) {
		const contextManager = this.getContextManager(gl);
		if (contextManager.hasResetThisFrame) return;
		contextManager.previousActiveRenderTextureMaxCount = 0;
		contextManager.hasResetThisFrame = true;
	}
	/**
	* モデルの描画が終わった後に呼び出すフレーム終了時の処理
	*
	* @param gl WebGLRenderingContextまたはWebGL2RenderingContext
	*/
	endFrameProcess(gl) {
		const contextManager = this.getContextManager(gl);
		contextManager.hasResetThisFrame = false;
	}
	/**
	* コンテナサイズの取得
	*
	* @param gl WebGLRenderingContextまたはWebGL2RenderingContext
	*/
	getContainerSize(gl) {
		const contextManager = this.getContextManager(gl);
		if (contextManager.offscreenRenderTargetContainers == null) return 0;
		return contextManager.offscreenRenderTargetContainers.length;
	}
	/**
	* 使用可能なリソースコンテナの取得
	*
	* @param gl WebGLRenderingContextまたはWebGL2RenderingContext
	* @param width 幅
	* @param height 高さ
	* @param previousFramebuffer 前のフレームバッファ
	* @return 使用可能なリソースコンテナ
	*/
	getOffscreenRenderTargetContainers(gl, width, height, previousFramebuffer) {
		const contextManager = this.getContextManager(gl);
		if (contextManager.width != width || contextManager.height != height || contextManager.offscreenRenderTargetContainers == null) this.initialize(gl, width, height);
		this.updateRenderTargetContainerCount(gl);
		const container = this.getUnusedOffscreenRenderTargetContainer(gl);
		if (container != null) return container;
		return this.createOffscreenRenderTargetContainer(gl, width, height, previousFramebuffer);
	}
	/**
	* リソースコンテナの使用状態を取得
	*
	* @param gl WebGLRenderingContextまたはWebGL2RenderingContext
	* @param renderTexture WebGLFramebuffer
	* @return 使用中はtrue、未使用の場合はfalse
	*/
	getUsingRenderTextureState(gl, renderTexture) {
		const contextManager = this.getContextManager(gl);
		for (let index = 0; index < contextManager.offscreenRenderTargetContainers.length; ++index) if (contextManager.offscreenRenderTargetContainers[index].renderTexture == renderTexture) return contextManager.offscreenRenderTargetContainers[index].inUse;
		return true;
	}
	/**
	* リソースコンテナの使用を開始する。
	*
	* @param gl WebGLRenderingContextまたはWebGL2RenderingContext
	* @param renderTexture WebGLFramebuffer
	*/
	startUsingRenderTexture(gl, renderTexture) {
		const contextManager = this.getContextManager(gl);
		for (let index = 0; index < contextManager.offscreenRenderTargetContainers.length; ++index) {
			if (contextManager.offscreenRenderTargetContainers[index].renderTexture != renderTexture) continue;
			contextManager.offscreenRenderTargetContainers[index].inUse = true;
			this.updateRenderTargetContainerCount(gl);
			break;
		}
	}
	/**
	* リソースコンテナの使用を終了する。
	*
	* @param gl WebGLRenderingContextまたはWebGL2RenderingContext
	* @param renderTexture WebGLFramebuffer
	*/
	stopUsingRenderTexture(gl, renderTexture) {
		const contextManager = this.getContextManager(gl);
		for (let index = 0; index < contextManager.offscreenRenderTargetContainers.length; ++index) {
			if (contextManager.offscreenRenderTargetContainers[index].renderTexture != renderTexture) continue;
			contextManager.offscreenRenderTargetContainers[index].inUse = false;
			contextManager.currentActiveRenderTextureCount--;
			if (contextManager.currentActiveRenderTextureCount < 0) contextManager.currentActiveRenderTextureCount = 0;
			break;
		}
	}
	/**
	* リソースコンテナの使用を全て終了する。
	*
	* @param gl WebGLRenderingContextまたはWebGL2RenderingContext
	*/
	stopUsingAllRenderTextures(gl) {
		const contextManager = this.getContextManager(gl);
		for (let index = 0; index < contextManager.offscreenRenderTargetContainers.length; ++index) contextManager.offscreenRenderTargetContainers[index].inUse = false;
		contextManager.currentActiveRenderTextureCount = 0;
	}
	/**
	* 使用されていないリソースコンテナを解放する。
	*
	* @param gl WebGLRenderingContextまたはWebGL2RenderingContext
	*/
	releaseStaleRenderTextures(gl) {
		const contextManager = this.getContextManager(gl);
		const listSize = contextManager.offscreenRenderTargetContainers.length;
		if (contextManager.hasResetThisFrame || listSize === 0) return;
		let findPos = 0;
		let resize = contextManager.previousActiveRenderTextureMaxCount;
		for (let i = listSize; contextManager.previousActiveRenderTextureMaxCount < i; --i) {
			const index = i - 1;
			if (contextManager.offscreenRenderTargetContainers[index].inUse) {
				let isFind = false;
				for (; findPos < contextManager.previousActiveRenderTextureMaxCount; ++findPos) if (!contextManager.offscreenRenderTargetContainers[findPos].inUse) {
					const tempContainer = contextManager.offscreenRenderTargetContainers[findPos];
					contextManager.offscreenRenderTargetContainers[findPos] = contextManager.offscreenRenderTargetContainers[index];
					contextManager.offscreenRenderTargetContainers[findPos].inUse = true;
					contextManager.offscreenRenderTargetContainers[index] = tempContainer;
					contextManager.offscreenRenderTargetContainers[index].inUse = false;
					isFind = true;
					break;
				}
				if (!isFind) {
					resize = i;
					break;
				}
			}
			const container = contextManager.offscreenRenderTargetContainers[index];
			contextManager.gl.bindTexture(contextManager.gl.TEXTURE_2D, null);
			contextManager.gl.deleteTexture(container.colorBuffer);
			contextManager.gl.bindFramebuffer(contextManager.gl.FRAMEBUFFER, null);
			contextManager.gl.deleteFramebuffer(container.renderTexture);
			container.clear();
		}
		updateSize(contextManager.offscreenRenderTargetContainers, resize);
	}
	/**
	* 直前のアクティブなレンダーターゲットの最大数を取得
	*
	* @param gl WebGLRenderingContextまたはWebGL2RenderingContext
	* @returns 直前のアクティブなレンダーターゲットの最大数
	*/
	getPreviousActiveRenderTextureCount(gl) {
		return this.getContextManager(gl).previousActiveRenderTextureMaxCount;
	}
	/**
	* 現在のアクティブなレンダーターゲットの数を取得
	*
	* @param gl WebGLRenderingContextまたはWebGL2RenderingContext
	* @returns 現在のアクティブなレンダーターゲットの数
	*/
	getCurrentActiveRenderTextureCount(gl) {
		return this.getContextManager(gl).currentActiveRenderTextureCount;
	}
	/**
	* 現在のアクティブなレンダーターゲットの数を更新
	*
	* @param gl WebGLRenderingContextまたはWebGL2RenderingContext
	*/
	updateRenderTargetContainerCount(gl) {
		const contextManager = this.getContextManager(gl);
		++contextManager.currentActiveRenderTextureCount;
		contextManager.previousActiveRenderTextureMaxCount = contextManager.currentActiveRenderTextureCount > contextManager.previousActiveRenderTextureMaxCount ? contextManager.currentActiveRenderTextureCount : contextManager.previousActiveRenderTextureMaxCount;
	}
	/**
	* 使用されていないリソースコンテナの取得
	*
	* @param gl WebGLRenderingContextまたはWebGL2RenderingContext
	* @return 使用されていないリソースコンテナ
	*/
	getUnusedOffscreenRenderTargetContainer(gl) {
		const contextManager = this.getContextManager(gl);
		for (let index = 0; index < contextManager.offscreenRenderTargetContainers.length; ++index) {
			const container = contextManager.offscreenRenderTargetContainers[index];
			if (container.inUse == false) {
				container.inUse = true;
				return container;
			}
		}
		return null;
	}
	/**
	* 新たにリソースコンテナを作成する。
	*
	* @param gl WebGLRenderingContextまたはWebGL2RenderingContext
	* @param width 幅
	* @param height 高さ
	* @param previousFramebuffer 前のフレームバッファ
	* @return 作成されたリソースコンテナ
	*/
	createOffscreenRenderTargetContainer(gl, width, height, previousFramebuffer) {
		const renderTarget = new CubismRenderTarget_WebGL();
		if (!renderTarget.createRenderTarget(gl, width, height, previousFramebuffer)) {
			CubismLogError("Failed to create offscreen render texture.");
			return null;
		}
		const offscreenRenderTextureContainer = new CubismRenderTargetContainer(renderTarget.getColorBuffer(), renderTarget.getRenderTexture(), true);
		this.getContextManager(gl).offscreenRenderTargetContainers.push(offscreenRenderTextureContainer);
		return offscreenRenderTextureContainer;
	}
};
// examples/packages/live2d_pet/vendor/Framework/src/rendering/cubismoffscreenrendertarget_webgl.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
/**
* WebGL用オフスクリーンサーフェス
* マスクの描画及びオフスクリーン機能に必要なフレームバッファなどを管理する。
*/
var CubismOffscreenRenderTarget_WebGL = class extends CubismRenderTarget_WebGL {
	/**
	* リソースコンテナマネージャを初期化する。
	*
	* @param displayBufferWidth レンダーターゲットの幅
	* @param displayBufferHeight レンダーターゲットの高さ
	*/
	initializeOffscreenManager(gl, displayBufferWidth, displayBufferHeight) {
		this._gl = gl;
		this._webGLOffscreenManager = CubismWebGLOffscreenManager.getInstance();
		if (this._webGLOffscreenManager.getContainerSize(gl) === 0) this._webGLOffscreenManager.initialize(gl, displayBufferWidth, displayBufferHeight);
	}
	/**
	* オフスクリーン描画用レンダーターゲットをセットする。
	*
	* @param gl WebGLRenderingContextまたはWebGL2RenderingContext
	*          NOTE: Cubism 5.3以降のモデルが使用される場合はWebGL2RenderingContextを使用すること。
	* @param displayBufferWidth レンダーターゲットの幅
	* @param displayBufferHeight レンダーターゲットの高さ
	* @param previousFramebuffer 前のフレームバッファ
	*/
	setOffscreenRenderTarget(gl, displayBufferWidth, displayBufferHeight, previousFramebuffer) {
		if (this._webGLOffscreenManager == null) this.initializeOffscreenManager(gl, displayBufferWidth, displayBufferHeight);
		const offscreenRenderTargetContainer = this._webGLOffscreenManager.getOffscreenRenderTargetContainers(gl, displayBufferWidth, displayBufferHeight, previousFramebuffer);
		if (offscreenRenderTargetContainer == null) {
			CubismLogError("Failed to acquire offscreen render texture container.");
			return;
		}
		this._colorBuffer = offscreenRenderTargetContainer.getColorBuffer();
		this._renderTexture = offscreenRenderTargetContainer.getRenderTexture();
		this._bufferWidth = displayBufferWidth;
		this._bufferHeight = displayBufferHeight;
		this._gl = gl;
		if (this._renderTexture == null) {
			this._renderTexture = previousFramebuffer;
			CubismLogError("Failed to create offscreen render texture.");
		}
	}
	/**
	* リソースコンテナの使用状態を取得
	*
	* @return 使用中はtrue、未使用の場合はfalse
	*/
	getUsingRenderTextureState() {
		if (this._webGLOffscreenManager == null || this._gl == null) return true;
		return this._webGLOffscreenManager.getUsingRenderTextureState(this._gl, this._renderTexture);
	}
	/**
	* リソースコンテナの使用を開始する。
	*/
	startUsingRenderTexture() {
		if (this._webGLOffscreenManager == null || this._gl == null) return;
		this._webGLOffscreenManager.startUsingRenderTexture(this._gl, this._renderTexture);
	}
	/**
	* リソースコンテナの使用を終了する。
	*/
	stopUsingRenderTexture() {
		if (this._webGLOffscreenManager == null || this._gl == null) return;
		this._webGLOffscreenManager.stopUsingRenderTexture(this._gl, this._renderTexture);
	}
	/**
	* オフスクリーンのインデックスを設定する。
	*
	* @param offscreenIndex オフスクリーンのインデックス
	*/
	setOffscreenIndex(offscreenIndex) {
		this._offscreenIndex = offscreenIndex;
	}
	/**
	* オフスクリーンのインデックスを取得する。
	*
	* @return オフスクリーンのインデックス
	*/
	getOffscreenIndex() {
		return this._offscreenIndex;
	}
	/**
	* 以前のオフスクリーン描画用レンダーターゲットを設定する。
	*
	* @param oldOffscreen 以前のオフスクリーン描画用レンダーターゲット
	*/
	setOldOffscreen(oldOffscreen) {
		this._oldOffscreen = oldOffscreen;
	}
	/**
	* 以前のオフスクリーン描画用レンダーターゲットを取得する。
	*
	* @return 以前のオフスクリーン描画用レンダーターゲット
	*/
	getOldOffscreen() {
		return this._oldOffscreen;
	}
	/**
	* 親のオフスクリーン描画用レンダーターゲットを設定する。
	*
	* @param parentOffscreenRenderTarget 親のオフスクリーン描画用レンダーターゲット
	*/
	setParentPartOffscreen(parentOffscreenRenderTarget) {
		this._parentOffscreenRenderTarget = parentOffscreenRenderTarget;
	}
	/**
	* 親のオフスクリーン描画用レンダーターゲットを取得する。
	*
	* @return 親のオフスクリーン描画用レンダーターゲット
	*/
	getParentPartOffscreen() {
		return this._parentOffscreenRenderTarget;
	}
	/**
	* コンストラクタ
	*/
	constructor() {
		super();
		this._offscreenIndex = -1;
		this._parentOffscreenRenderTarget = null;
		this._oldOffscreen = null;
		this._webGLOffscreenManager = null;
	}
	release() {
		if (this._webGLOffscreenManager != null && this._gl != null && this._renderTexture != null) this._webGLOffscreenManager.stopUsingRenderTexture(this._gl, this._renderTexture);
		if (this._colorBuffer && this._gl) {
			this._gl.deleteTexture(this._colorBuffer);
			this._colorBuffer = null;
		}
		if (this._renderTexture && this._gl) {
			this._gl.deleteFramebuffer(this._renderTexture);
			this._renderTexture = null;
		}
		if (this._webGLOffscreenManager != null) this._webGLOffscreenManager = null;
		this._oldOffscreen = null;
		this._parentOffscreenRenderTarget = null;
	}
};
// examples/packages/live2d_pet/vendor/Framework/src/rendering/cubismrenderer_webgl.ts
var s_invalidValue = -1;
var s_renderTargetIndexArray = new Uint16Array([
	0,
	1,
	2,
	2,
	1,
	3
]);
/**
* クリッピングマスクの処理を実行するクラス
*/
var CubismClippingManager_WebGL = class extends CubismClippingManager {
	/**
	* WebGLレンダリングコンテキストを設定する
	*
	* @param gl WebGLレンダリングコンテキスト
	*/
	setGL(gl) {
		this.gl = gl;
	}
	/**
	* コンストラクタ
	*/
	constructor() {
		super(CubismClippingContext_WebGL);
	}
	/**
	* クリッピングコンテキストを作成する。モデル描画時に実行する。
	*
	* @param model モデルのインスタンス
	* @param renderer レンダラのインスタンス
	* @param lastFbo フレームバッファ
	* @param lastViewport ビューポート
	* @param drawObjectType 描画オブジェクトのタイプ
	*/
	setupClippingContext(model, renderer, lastFbo, lastViewport, drawObjectType) {
		let usingClipCount = 0;
		for (let clipIndex = 0; clipIndex < this._clippingContextListForMask.length; clipIndex++) {
			const cc = this._clippingContextListForMask[clipIndex];
			switch (drawObjectType) {
				case DrawableObjectType.DrawableObjectType_Drawable:
				default:
					this.calcClippedDrawableTotalBounds(model, cc);
					break;
				case DrawableObjectType.DrawableObjectType_Offscreen: this.calcClippedOffscreenTotalBounds(model, cc);
			}
			if (cc._isUsing) usingClipCount++;
		}
		if (usingClipCount <= 0) return;
		this.gl.viewport(0, 0, this._clippingMaskBufferSize, this._clippingMaskBufferSize);
		switch (drawObjectType) {
			case DrawableObjectType.DrawableObjectType_Drawable:
			default:
				this._currentMaskBuffer = renderer.getDrawableMaskBuffer(0);
				break;
			case DrawableObjectType.DrawableObjectType_Offscreen: this._currentMaskBuffer = renderer.getOffscreenMaskBuffer(0);
		}
		this._currentMaskBuffer.beginDraw(lastFbo);
		renderer.preDraw();
		this.setupLayoutBounds(usingClipCount);
		if (this._clearedMaskBufferFlags.length != this._renderTextureCount) {
			this._clearedMaskBufferFlags.length = 0;
			this._clearedMaskBufferFlags = new Array(this._renderTextureCount);
			for (let i = 0; i < this._clearedMaskBufferFlags.length; i++) this._clearedMaskBufferFlags[i] = false;
		}
		for (let index = 0; index < this._clearedMaskBufferFlags.length; index++) this._clearedMaskBufferFlags[index] = false;
		for (let clipIndex = 0; clipIndex < this._clippingContextListForMask.length; clipIndex++) {
			const clipContext = this._clippingContextListForMask[clipIndex];
			const allClipedDrawRect = clipContext._allClippedDrawRect;
			const layoutBoundsOnTex01 = clipContext._layoutBounds;
			const margin = .05;
			let scaleX = 0;
			let scaleY = 0;
			let maskBuffer;
			switch (drawObjectType) {
				case DrawableObjectType.DrawableObjectType_Drawable:
				default:
					maskBuffer = renderer.getDrawableMaskBuffer(clipContext._bufferIndex);
					break;
				case DrawableObjectType.DrawableObjectType_Offscreen: maskBuffer = renderer.getOffscreenMaskBuffer(clipContext._bufferIndex);
			}
			if (this._currentMaskBuffer != maskBuffer) {
				this._currentMaskBuffer.endDraw();
				this._currentMaskBuffer = maskBuffer;
				this._currentMaskBuffer.beginDraw(lastFbo);
				renderer.preDraw();
			}
			this._tmpBoundsOnModel.setRect(allClipedDrawRect);
			this._tmpBoundsOnModel.expand(allClipedDrawRect.width * margin, allClipedDrawRect.height * margin);
			scaleX = layoutBoundsOnTex01.width / this._tmpBoundsOnModel.width;
			scaleY = layoutBoundsOnTex01.height / this._tmpBoundsOnModel.height;
			this.createMatrixForMask(false, layoutBoundsOnTex01, scaleX, scaleY);
			clipContext._matrixForMask.setMatrix(this._tmpMatrixForMask.getArray());
			clipContext._matrixForDraw.setMatrix(this._tmpMatrixForDraw.getArray());
			if (drawObjectType == DrawableObjectType.DrawableObjectType_Offscreen) {
				const invertMvp = renderer.getMvpMatrix().getInvert();
				clipContext._matrixForDraw.multiplyByMatrix(invertMvp);
			}
			const clipDrawCount = clipContext._clippingIdCount;
			for (let i = 0; i < clipDrawCount; i++) {
				const clipDrawIndex = clipContext._clippingIdList[i];
				if (!model.getDrawableDynamicFlagVertexPositionsDidChange(clipDrawIndex)) continue;
				renderer.setIsCulling(model.getDrawableCulling(clipDrawIndex) != false);
				if (!this._clearedMaskBufferFlags[clipContext._bufferIndex]) {
					this.gl.clearColor(1, 1, 1, 1);
					this.gl.clear(this.gl.COLOR_BUFFER_BIT);
					this._clearedMaskBufferFlags[clipContext._bufferIndex] = true;
				}
				renderer.setClippingContextBufferForMask(clipContext);
				renderer.drawMeshWebGL(model, clipDrawIndex);
			}
		}
		this._currentMaskBuffer.endDraw();
		renderer.setClippingContextBufferForMask(null);
		this.gl.viewport(lastViewport[0], lastViewport[1], lastViewport[2], lastViewport[3]);
	}
	/**
	* マスクの合計数をカウント
	*
	* @return マスクの合計数を返す
	*/
	getClippingMaskCount() {
		return this._clippingContextListForMask.length;
	}
};
/**
* クリッピングマスクのコンテキスト
*/
var CubismClippingContext_WebGL = class extends CubismClippingContext {
	/**
	* 引数付きコンストラクタ
	*
	* @param manager マスクを管理しているマネージャのインスタンス
	* @param clippingDrawableIndices クリップしているDrawableのインデックスリスト
	* @param clipCount クリップしているDrawableの個数
	*/
	constructor(manager, clippingDrawableIndices, clipCount) {
		super(clippingDrawableIndices, clipCount);
		this._owner = manager;
	}
	/**
	* このマスクを管理するマネージャのインスタンスを取得する
	*
	* @return クリッピングマネージャのインスタンス
	*/
	getClippingManager() {
		return this._owner;
	}
	/**
	* WebGLレンダリングコンテキストを設定する
	*
	* @param gl WebGLレンダリングコンテキスト
	*/
	setGl(gl) {
		this._owner.setGL(gl);
	}
};
/**
* Cubismモデルを描画する直前のWebGLのステートを保持・復帰させるクラス
*/
var CubismRendererProfile_WebGL = class {
	/**
	* WebGLの有効・無効をセットする
	*
	* @param index 有効・無効にする機能
	* @param enabled trueなら有効にする
	*/
	setGlEnable(index, enabled) {
		if (enabled) this.gl.enable(index);
		else this.gl.disable(index);
	}
	/**
	* WebGLのVertex Attribute Array機能の有効・無効をセットする
	*
	* @param   index   有効・無効にする機能
	* @param   enabled trueなら有効にする
	*/
	setGlEnableVertexAttribArray(index, enabled) {
		if (enabled) this.gl.enableVertexAttribArray(index);
		else this.gl.disableVertexAttribArray(index);
	}
	/**
	* WebGLのステートを保持する
	*/
	save() {
		if (this.gl == null) {
			CubismLogError("'gl' is null. WebGLRenderingContext is required.\nPlease call 'CubimRenderer_WebGL.startUp' function.");
			return;
		}
		this._lastArrayBufferBinding = this.gl.getParameter(this.gl.ARRAY_BUFFER_BINDING);
		this._lastElementArrayBufferBinding = this.gl.getParameter(this.gl.ELEMENT_ARRAY_BUFFER_BINDING);
		this._lastProgram = this.gl.getParameter(this.gl.CURRENT_PROGRAM);
		this._lastActiveTexture = this.gl.getParameter(this.gl.ACTIVE_TEXTURE);
		this.gl.activeTexture(this.gl.TEXTURE1);
		this._lastTexture1Binding2D = this.gl.getParameter(this.gl.TEXTURE_BINDING_2D);
		this.gl.activeTexture(this.gl.TEXTURE0);
		this._lastTexture0Binding2D = this.gl.getParameter(this.gl.TEXTURE_BINDING_2D);
		this._lastVertexAttribArrayEnabled[0] = this.gl.getVertexAttrib(0, this.gl.VERTEX_ATTRIB_ARRAY_ENABLED);
		this._lastVertexAttribArrayEnabled[1] = this.gl.getVertexAttrib(1, this.gl.VERTEX_ATTRIB_ARRAY_ENABLED);
		this._lastVertexAttribArrayEnabled[2] = this.gl.getVertexAttrib(2, this.gl.VERTEX_ATTRIB_ARRAY_ENABLED);
		this._lastVertexAttribArrayEnabled[3] = this.gl.getVertexAttrib(3, this.gl.VERTEX_ATTRIB_ARRAY_ENABLED);
		this._lastScissorTest = this.gl.isEnabled(this.gl.SCISSOR_TEST);
		this._lastStencilTest = this.gl.isEnabled(this.gl.STENCIL_TEST);
		this._lastDepthTest = this.gl.isEnabled(this.gl.DEPTH_TEST);
		this._lastCullFace = this.gl.isEnabled(this.gl.CULL_FACE);
		this._lastBlend = this.gl.isEnabled(this.gl.BLEND);
		this._lastFrontFace = this.gl.getParameter(this.gl.FRONT_FACE);
		this._lastColorMask = this.gl.getParameter(this.gl.COLOR_WRITEMASK);
		this._lastBlending[0] = this.gl.getParameter(this.gl.BLEND_SRC_RGB);
		this._lastBlending[1] = this.gl.getParameter(this.gl.BLEND_DST_RGB);
		this._lastBlending[2] = this.gl.getParameter(this.gl.BLEND_SRC_ALPHA);
		this._lastBlending[3] = this.gl.getParameter(this.gl.BLEND_DST_ALPHA);
	}
	/**
	* 保持したWebGLのステートを復帰させる
	*/
	restore() {
		if (this.gl == null) {
			CubismLogError("'gl' is null. WebGLRenderingContext is required.\nPlease call 'CubimRenderer_WebGL.startUp' function.");
			return;
		}
		this.gl.useProgram(this._lastProgram);
		this.setGlEnableVertexAttribArray(0, this._lastVertexAttribArrayEnabled[0]);
		this.setGlEnableVertexAttribArray(1, this._lastVertexAttribArrayEnabled[1]);
		this.setGlEnableVertexAttribArray(2, this._lastVertexAttribArrayEnabled[2]);
		this.setGlEnableVertexAttribArray(3, this._lastVertexAttribArrayEnabled[3]);
		this.setGlEnable(this.gl.SCISSOR_TEST, this._lastScissorTest);
		this.setGlEnable(this.gl.STENCIL_TEST, this._lastStencilTest);
		this.setGlEnable(this.gl.DEPTH_TEST, this._lastDepthTest);
		this.setGlEnable(this.gl.CULL_FACE, this._lastCullFace);
		this.setGlEnable(this.gl.BLEND, this._lastBlend);
		this.gl.frontFace(this._lastFrontFace);
		this.gl.colorMask(this._lastColorMask[0], this._lastColorMask[1], this._lastColorMask[2], this._lastColorMask[3]);
		this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this._lastArrayBufferBinding);
		this.gl.bindBuffer(this.gl.ELEMENT_ARRAY_BUFFER, this._lastElementArrayBufferBinding);
		this.gl.activeTexture(this.gl.TEXTURE1);
		this.gl.bindTexture(this.gl.TEXTURE_2D, this._lastTexture1Binding2D);
		this.gl.activeTexture(this.gl.TEXTURE0);
		this.gl.bindTexture(this.gl.TEXTURE_2D, this._lastTexture0Binding2D);
		this.gl.activeTexture(this._lastActiveTexture);
		this.gl.blendFuncSeparate(this._lastBlending[0], this._lastBlending[1], this._lastBlending[2], this._lastBlending[3]);
	}
	/**
	* WebGLレンダリングコンテキストを設定する
	*
	* @param gl WebGLレンダリングコンテキスト
	*/
	setGl(gl) {
		this.gl = gl;
	}
	/**
	* コンストラクタ
	*/
	constructor() {
		this._lastVertexAttribArrayEnabled = new Array(4);
		this._lastColorMask = new Array(4);
		this._lastBlending = new Array(4);
	}
};
/**
* WebGL用の描画命令を実装したクラス
*/
var CubismRenderer_WebGL = class extends CubismRenderer {
	/**
	* レンダラの初期化処理を実行する
	* 引数に渡したモデルからレンダラの初期化処理に必要な情報を取り出すことができる
	* NOTE: WebGLコンテキストが初期化されていない可能性があるため、ここではWebGLコンテキストを使う初期化は行わない。
	*
	* @param model モデルのインスタンス
	* @param maskBufferCount バッファの生成数
	*/
	initialize(model, maskBufferCount = 1) {
		if (model.isUsingMasking()) {
			this._drawableClippingManager = new CubismClippingManager_WebGL();
			this._drawableClippingManager.initializeForDrawable(model, maskBufferCount);
		}
		if (model.isUsingMaskingForOffscreen()) {
			this._offscreenClippingManager = new CubismClippingManager_WebGL();
			this._offscreenClippingManager.initializeForOffscreen(model, maskBufferCount);
		}
		updateSize(this._sortedObjectsIndexList, model.getDrawableCount() + (model.getOffscreenCount ? model.getOffscreenCount() : 0), 0, true);
		updateSize(this._sortedObjectsTypeList, model.getDrawableCount() + (model.getOffscreenCount ? model.getOffscreenCount() : 0), 0, true);
		super.initialize(model);
	}
	/**
	* オフスクリーンの親を探して設定する
	*
	* @param model モデルのインスタンス
	* @param offscreenCount オフスクリーンの数
	*/
	setupParentOffscreens(model, offscreenCount) {
		let parentOffscreen;
		for (let offscreenIndex = 0; offscreenIndex < offscreenCount; ++offscreenIndex) {
			parentOffscreen = null;
			const ownerIndex = model.getOffscreenOwnerIndices()[offscreenIndex];
			let parentIndex = model.getPartParentPartIndices()[ownerIndex];
			while (parentIndex != -1) {
				for (let i = 0; i < offscreenCount; ++i) {
					if (model.getOffscreenOwnerIndices()[this._offscreenList[i].getOffscreenIndex()] != parentIndex) continue;
					parentOffscreen = this._offscreenList[i];
					break;
				}
				if (parentOffscreen != null) break;
				parentIndex = model.getPartParentPartIndices()[parentIndex];
			}
			this._offscreenList[offscreenIndex].setParentPartOffscreen(parentOffscreen);
		}
	}
	/**
	* WebGLテクスチャのバインド処理
	* CubismRendererにテクスチャを設定し、CubismRenderer内でその画像を参照するためのIndex値を戻り値とする
	*
	* @param modelTextureNo セットするモデルテクスチャの番号
	* @param glTextureNo WebGLテクスチャの番号
	*/
	bindTexture(modelTextureNo, glTexture) {
		this._textures.set(modelTextureNo, glTexture);
	}
	/**
	* WebGLにバインドされたテクスチャのリストを取得する
	*
	* @return テクスチャのリスト
	*/
	getBindedTextures() {
		return this._textures;
	}
	/**
	* クリッピングマスクバッファのサイズを設定する
	* マスク用のFrameBufferを破棄、再作成する為処理コストは高い
	*
	* @param size クリッピングマスクバッファのサイズ
	*/
	setClippingMaskBufferSize(size) {
		if (!this._model.isUsingMasking()) return;
		const renderTextureCount = this._drawableClippingManager.getRenderTextureCount();
		this._drawableClippingManager.release();
		this._drawableClippingManager = void 0;
		this._drawableClippingManager = null;
		this._drawableClippingManager = new CubismClippingManager_WebGL();
		this._drawableClippingManager.setClippingMaskBufferSize(size);
		this._drawableClippingManager.initializeForDrawable(this.getModel(), renderTextureCount);
	}
	/**
	* クリッピングマスクバッファのサイズを取得する
	*
	* @return クリッピングマスクバッファのサイズ
	*/
	getClippingMaskBufferSize() {
		return this._model.isUsingMasking() ? this._drawableClippingManager.getClippingMaskBufferSize() : s_invalidValue;
	}
	/**
	* ブレンドモード用のフレームバッファを取得する
	*
	* @return ブレンドモード用のフレームバッファ
	*/
	getModelRenderTarget(index) {
		return this._modelRenderTargets[index];
	}
	/**
	* レンダーテクスチャの枚数を取得する
	* @return レンダーテクスチャの枚数
	*/
	getRenderTextureCount() {
		return this._model.isUsingMasking() ? this._drawableClippingManager.getRenderTextureCount() : s_invalidValue;
	}
	/**
	* コンストラクタ
	*/
	constructor(width, height) {
		super(width, height);
		this._clippingContextBufferForMask = null;
		this._clippingContextBufferForDraw = null;
		this._rendererProfile = new CubismRendererProfile_WebGL();
		this._textures = /* @__PURE__ */ new Map();
		this._sortedObjectsIndexList = new Array();
		this._sortedObjectsTypeList = new Array();
		this._bufferData = {
			vertex: WebGLBuffer = null,
			uv: WebGLBuffer = null,
			index: WebGLBuffer = null
		};
		this._modelRenderTargets = new Array();
		this._drawableMasks = new Array();
		this._currentFbo = null;
		this._drawableClippingManager = null;
		this._offscreenClippingManager = null;
		this._offscreenMasks = new Array();
		this._offscreenList = new Array();
	}
	/**
	* デストラクタ相当の処理
	*/
	release() {
		if (this._drawableClippingManager) {
			this._drawableClippingManager.release();
			this._drawableClippingManager = void 0;
			this._drawableClippingManager = null;
		}
		if (this.gl == null) return;
		this.gl.deleteBuffer(this._bufferData.vertex);
		this._bufferData.vertex = null;
		this.gl.deleteBuffer(this._bufferData.uv);
		this._bufferData.uv = null;
		this.gl.deleteBuffer(this._bufferData.index);
		this._bufferData.index = null;
		this._bufferData = null;
		this._textures = null;
		for (let i = 0; i < this._modelRenderTargets.length; i++) if (this._modelRenderTargets[i] != null && this._modelRenderTargets[i].isValid()) this._modelRenderTargets[i].destroyRenderTarget();
		this._modelRenderTargets.length = 0;
		this._modelRenderTargets = null;
		for (let i = 0; i < this._drawableMasks.length; i++) if (this._drawableMasks[i] != null && this._drawableMasks[i].isValid()) this._drawableMasks[i].destroyRenderTarget();
		this._drawableMasks.length = 0;
		this._drawableMasks = null;
		for (let i = 0; i < this._offscreenMasks.length; i++) if (this._offscreenMasks[i] != null && this._offscreenMasks[i].isValid()) this._offscreenMasks[i].destroyRenderTarget();
		this._offscreenMasks.length = 0;
		this._offscreenMasks = null;
		for (let i = 0; i < this._offscreenList.length; i++) if (this._offscreenList[i] != null && this._offscreenList[i].isValid()) this._offscreenList[i].destroyRenderTarget();
		this._offscreenList.length = 0;
		this._offscreenList = null;
		this._offscreenClippingManager = null;
		this._drawableClippingManager = null;
		this._clippingContextBufferForMask = null;
		this._clippingContextBufferForDraw = null;
		this._rendererProfile = null;
		this._sortedObjectsIndexList = null;
		this._sortedObjectsTypeList = null;
		this._currentFbo = null;
		this._model = null;
		this.gl = null;
	}
	/**
	* Shaderの読み込みを行う
	* @param shaderPath シェーダのパス
	*/
	loadShaders(shaderPath = null) {
		if (this.gl == null) {
			CubismLogError("'gl' is null. WebGLRenderingContext is required.\nPlease call 'CubimRenderer_WebGL.startUp' function.");
			return;
		}
		if (CubismShaderManager_WebGL.getInstance().getShader(this.gl)._shaderSets.length == 0 || !CubismShaderManager_WebGL.getInstance().getShader(this.gl)._isShaderLoaded) {
			const shader = CubismShaderManager_WebGL.getInstance().getShader(this.gl);
			if (shaderPath != null) shader.setShaderPath(shaderPath);
			shader.generateShaders();
		}
	}
	/**
	* モデルを描画する実際の処理
	* @param shaderPath シェーダのパス
	*/
	doDrawModel(shaderPath = null) {
		this.loadShaders(shaderPath);
		this.beforeDrawModelRenderTarget();
		const lastFbo = this.gl.getParameter(this.gl.FRAMEBUFFER_BINDING);
		const lastViewport = this.gl.getParameter(this.gl.VIEWPORT);
		if (this._drawableClippingManager != null) {
			this.preDraw();
			for (let i = 0; i < this._drawableClippingManager.getRenderTextureCount(); ++i) if (this._drawableMasks[i].getBufferWidth() != this._drawableClippingManager.getClippingMaskBufferSize() || this._drawableMasks[i].getBufferHeight() != this._drawableClippingManager.getClippingMaskBufferSize()) this._drawableMasks[i].createRenderTarget(this.gl, this._drawableClippingManager.getClippingMaskBufferSize(), this._drawableClippingManager.getClippingMaskBufferSize(), lastFbo);
			if (this.isUsingHighPrecisionMask()) this._drawableClippingManager.setupMatrixForHighPrecision(this.getModel(), false);
			else this._drawableClippingManager.setupClippingContext(this.getModel(), this, lastFbo, lastViewport, DrawableObjectType.DrawableObjectType_Drawable);
		}
		if (this._offscreenClippingManager != null) {
			this.preDraw();
			for (let i = 0; i < this._offscreenClippingManager.getRenderTextureCount(); ++i) if (this._offscreenMasks[i].getBufferWidth() != this._offscreenClippingManager.getClippingMaskBufferSize() || this._offscreenMasks[i].getBufferHeight() != this._offscreenClippingManager.getClippingMaskBufferSize()) this._offscreenMasks[i].createRenderTarget(this.gl, this._offscreenClippingManager.getClippingMaskBufferSize(), this._offscreenClippingManager.getClippingMaskBufferSize(), lastFbo);
			if (this.isUsingHighPrecisionMask()) this._offscreenClippingManager.setupMatrixForOffscreenHighPrecision(this.getModel(), false, this.getMvpMatrix());
			else this._offscreenClippingManager.setupClippingContext(this.getModel(), this, lastFbo, lastViewport, DrawableObjectType.DrawableObjectType_Offscreen);
		}
		this.preDraw();
		this.drawObjectLoop(lastFbo);
		this.afterDrawModelRenderTarget();
	}
	/**
	* 描画オブジェクトのループ処理を行う。
	*
	* @param lastFbo 前回のフレームバッファ
	*/
	drawObjectLoop(lastFbo) {
		const model = this.getModel();
		const drawableCount = model.getDrawableCount();
		const totalCount = drawableCount + model.getOffscreenCount();
		const renderOrder = model.getRenderOrders();
		this._currentOffscreen = null;
		this._currentFbo = lastFbo;
		this._modelRootFbo = lastFbo;
		for (let i = 0; i < totalCount; ++i) {
			const order = renderOrder[i];
			if (i < drawableCount) {
				this._sortedObjectsIndexList[order] = i;
				this._sortedObjectsTypeList[order] = DrawableObjectType.DrawableObjectType_Drawable;
			} else if (i < totalCount) {
				this._sortedObjectsIndexList[order] = i - drawableCount;
				this._sortedObjectsTypeList[order] = DrawableObjectType.DrawableObjectType_Offscreen;
			}
		}
		for (let i = 0; i < totalCount; ++i) {
			const objectIndex = this._sortedObjectsIndexList[i];
			const objectType = this._sortedObjectsTypeList[i];
			this.renderObject(objectIndex, objectType);
		}
		while (this._currentOffscreen != null) this.submitDrawToParentOffscreen(this._currentOffscreen.getOffscreenIndex(), DrawableObjectType.DrawableObjectType_Offscreen);
	}
	/**
	* 描画オブジェクトを描画する。
	*
	* @param objectIndex 描画対象のオブジェクトのインデックス
	* @param objectType 描画対象のオブジェクトのタイプ
	* @param lastFbo 前回のフレームバッファ
	* @param lastViewport 前回のビューポート
	*/
	renderObject(objectIndex, objectType) {
		switch (objectType) {
			case DrawableObjectType.DrawableObjectType_Drawable:
				this.drawDrawable(objectIndex, this._modelRootFbo);
				break;
			case DrawableObjectType.DrawableObjectType_Offscreen:
				this.addOffscreen(objectIndex);
				break;
			default: CubismLogError("Unknown object type: " + objectType);
		}
	}
	/**
	* 描画オブジェクト（アートメッシュ）を描画する。
	*
	* @param model 描画対象のモデル
	* @param index 描画対象のメッシュのインデックス
	*/
	drawDrawable(drawableIndex, rootFbo) {
		if (!this.getModel().getDrawableDynamicFlagIsVisible(drawableIndex)) return;
		this.submitDrawToParentOffscreen(drawableIndex, DrawableObjectType.DrawableObjectType_Drawable);
		const clipContext = this._drawableClippingManager != null ? this._drawableClippingManager.getClippingContextListForDraw()[drawableIndex] : null;
		if (clipContext != null && this.isUsingHighPrecisionMask()) {
			if (clipContext._isUsing) {
				this.gl.viewport(0, 0, this._drawableClippingManager.getClippingMaskBufferSize(), this._drawableClippingManager.getClippingMaskBufferSize());
				this.preDraw();
				this.getDrawableMaskBuffer(clipContext._bufferIndex).beginDraw(this._currentFbo);
				this.gl.clearColor(1, 1, 1, 1);
				this.gl.clear(this.gl.COLOR_BUFFER_BIT);
			}
			{
				const clipDrawCount = clipContext._clippingIdCount;
				for (let index = 0; index < clipDrawCount; index++) {
					const clipDrawIndex = clipContext._clippingIdList[index];
					if (!this._model.getDrawableDynamicFlagVertexPositionsDidChange(clipDrawIndex)) continue;
					this.setIsCulling(this._model.getDrawableCulling(clipDrawIndex) != false);
					this.setClippingContextBufferForMask(clipContext);
					this.drawMeshWebGL(this._model, clipDrawIndex);
				}
				this.getDrawableMaskBuffer(clipContext._bufferIndex).endDraw();
				this.setClippingContextBufferForMask(null);
				this.gl.viewport(0, 0, this._modelRenderTargetWidth, this._modelRenderTargetHeight);
				this.preDraw();
			}
		}
		this.setClippingContextBufferForDrawable(clipContext);
		this.setIsCulling(this.getModel().getDrawableCulling(drawableIndex));
		this.drawMeshWebGL(this._model, drawableIndex);
	}
	/**
	* 描画オブジェクト（アートメッシュ）を描画する。
	*
	* @param model 描画対象のモデル
	* @param index 描画対象のメッシュのインデックス
	*/
	drawMeshWebGL(model, index) {
		if (this.isCulling()) this.gl.enable(this.gl.CULL_FACE);
		else this.gl.disable(this.gl.CULL_FACE);
		this.gl.frontFace(this.gl.CCW);
		if (this.isGeneratingMask()) CubismShaderManager_WebGL.getInstance().getShader(this.gl).setupShaderProgramForMask(this, model, index);
		else CubismShaderManager_WebGL.getInstance().getShader(this.gl).setupShaderProgramForDrawable(this, model, index);
		if (!CubismShaderManager_WebGL.getInstance().getShader(this.gl)._isShaderLoaded) return;
		{
			const indexCount = model.getDrawableVertexIndexCount(index);
			this.gl.drawElements(this.gl.TRIANGLES, indexCount, this.gl.UNSIGNED_SHORT, 0);
		}
		this.gl.useProgram(null);
		this.setClippingContextBufferForDrawable(null);
		this.setClippingContextBufferForMask(null);
	}
	/**
	* オフスクリーンを親のオフスクリーンにコピーする。
	*
	* @param objectIndex オブジェクトのインデックス
	* @param objectType  オブジェクトの種類
	*/
	submitDrawToParentOffscreen(objectIndex, objectType) {
		if (this._currentOffscreen == null || objectIndex == s_invalidValue) return;
		const currentOwnerIndex = this.getModel().getOffscreenOwnerIndices()[this._currentOffscreen.getOffscreenIndex()];
		if (currentOwnerIndex == s_invalidValue) return;
		let targetParentIndex = -1;
		switch (objectType) {
			case DrawableObjectType.DrawableObjectType_Drawable:
				targetParentIndex = this.getModel().getDrawableParentPartIndex(objectIndex);
				break;
			case DrawableObjectType.DrawableObjectType_Offscreen:
				targetParentIndex = this.getModel().getPartParentPartIndices()[this.getModel().getOffscreenOwnerIndices()[objectIndex]];
				break;
			default: return;
		}
		while (targetParentIndex != -1) {
			if (targetParentIndex == currentOwnerIndex) return;
			targetParentIndex = this.getModel().getPartParentPartIndices()[targetParentIndex];
		}
		this.drawOffscreen(this._currentOffscreen);
		this.submitDrawToParentOffscreen(objectIndex, objectType);
	}
	/**
	* 描画オブジェクト（オフスクリーン）を追加する。
	*
	* @param offscreenIndex オフスクリーンのインデックス
	*/
	addOffscreen(offscreenIndex) {
		if (this._currentOffscreen != null && this._currentOffscreen.getOffscreenIndex() != offscreenIndex) {
			let isParent = false;
			const ownerIndex = this.getModel().getOffscreenOwnerIndices()[offscreenIndex];
			let parentIndex = this.getModel().getPartParentPartIndices()[ownerIndex];
			const currentOffscreenIndex = this._currentOffscreen.getOffscreenIndex();
			const currentOffscreenOwnerIndex = this.getModel().getOffscreenOwnerIndices()[currentOffscreenIndex];
			while (parentIndex != -1) {
				if (parentIndex == currentOffscreenOwnerIndex) {
					isParent = true;
					break;
				}
				parentIndex = this.getModel().getPartParentPartIndices()[parentIndex];
			}
			if (!isParent) this.submitDrawToParentOffscreen(offscreenIndex, DrawableObjectType.DrawableObjectType_Offscreen);
		}
		const offscreen = this._offscreenList[offscreenIndex];
		if (offscreen.getRenderTexture() == null || offscreen.getBufferWidth() != this._modelRenderTargetWidth || offscreen.getBufferHeight() != this._modelRenderTargetHeight || offscreen.getUsingRenderTextureState()) offscreen.setOffscreenRenderTarget(this.gl, this._modelRenderTargetWidth, this._modelRenderTargetHeight, this._currentFbo);
		else offscreen.startUsingRenderTexture();
		const oldOffscreen = offscreen.getParentPartOffscreen();
		offscreen.setOldOffscreen(oldOffscreen);
		let oldFBO = null;
		if (oldOffscreen != null) oldFBO = oldOffscreen.getRenderTexture();
		if (oldFBO == null) oldFBO = this._modelRootFbo;
		offscreen.beginDraw(oldFBO);
		this.gl.viewport(0, 0, this._modelRenderTargetWidth, this._modelRenderTargetHeight);
		offscreen.clear(0, 0, 0, 0);
		this._currentOffscreen = offscreen;
		this._currentFbo = offscreen.getRenderTexture();
	}
	/**
	* オフスクリーン描画を行う。
	*
	* @param offscreen オフスクリーンレンダリングターゲット
	*/
	drawOffscreen(offscreen) {
		const offscreenIndex = offscreen.getOffscreenIndex();
		const clipContext = this._offscreenClippingManager != null ? this._offscreenClippingManager.getClippingContextListForOffscreen()[offscreenIndex] : null;
		if (clipContext != null && this.isUsingHighPrecisionMask()) {
			if (clipContext._isUsing) {
				this.gl.viewport(0, 0, this._offscreenClippingManager.getClippingMaskBufferSize(), this._offscreenClippingManager.getClippingMaskBufferSize());
				this.preDraw();
				this.getOffscreenMaskBuffer(clipContext._bufferIndex).beginDraw(this._currentFbo);
				this.gl.clearColor(1, 1, 1, 1);
				this.gl.clear(this.gl.COLOR_BUFFER_BIT);
			}
			{
				const clipDrawCount = clipContext._clippingIdCount;
				for (let index = 0; index < clipDrawCount; index++) {
					const clipDrawIndex = clipContext._clippingIdList[index];
					if (!this.getModel().getDrawableDynamicFlagVertexPositionsDidChange(clipDrawIndex)) continue;
					this.setIsCulling(this.getModel().getDrawableCulling(clipDrawIndex) != false);
					this.setClippingContextBufferForMask(clipContext);
					this.drawMeshWebGL(this.getModel(), clipDrawIndex);
				}
			}
			this.getOffscreenMaskBuffer(clipContext._bufferIndex).endDraw();
			this.setClippingContextBufferForMask(null);
			this.gl.viewport(0, 0, this._modelRenderTargetWidth, this._modelRenderTargetHeight);
			this.preDraw();
		}
		this.setClippingContextBufferForOffscreen(clipContext);
		this.setIsCulling(this._model.getOffscreenCulling(offscreenIndex) != false);
		this.drawOffscreenWebGL(this.getModel(), offscreen);
	}
	/**
	* オフスクリーン描画のWebGL実装
	*
	* @param model モデル
	* @param index オフスクリーンインデックス
	*/
	drawOffscreenWebGL(model, offscreen) {
		if (this.isCulling()) this.gl.enable(this.gl.CULL_FACE);
		else this.gl.disable(this.gl.CULL_FACE);
		this.gl.frontFace(this.gl.CCW);
		CubismShaderManager_WebGL.getInstance().getShader(this.gl).setupShaderProgramForOffscreen(this, model, offscreen);
		offscreen.endDraw();
		this._currentOffscreen = this._currentOffscreen.getOldOffscreen();
		this._currentFbo = offscreen.getOldFBO();
		if (this._currentFbo == null) {
			this._currentOffscreen = this._modelRenderTargets[0];
			this._currentFbo = this._modelRenderTargets[0].getRenderTexture();
			this.gl.bindFramebuffer(this.gl.FRAMEBUFFER, this._currentFbo);
		}
		{
			const indexBuffer = this.gl.createBuffer();
			this.gl.bindBuffer(this.gl.ELEMENT_ARRAY_BUFFER, indexBuffer);
			this.gl.bufferData(this.gl.ELEMENT_ARRAY_BUFFER, s_renderTargetIndexArray, this.gl.STATIC_DRAW);
			this.gl.drawElements(this.gl.TRIANGLES, s_renderTargetIndexArray.length, this.gl.UNSIGNED_SHORT, 0);
			this.gl.deleteBuffer(indexBuffer);
		}
		offscreen.stopUsingRenderTexture();
		this.gl.useProgram(null);
		this.setClippingContextBufferForMask(null);
		this.setClippingContextBufferForOffscreen(null);
	}
	/**
	* モデル描画直前のレンダラのステートを保持する
	*/
	saveProfile() {
		this._rendererProfile.save();
	}
	/**
	* モデル描画直前のレンダラのステートを復帰させる
	*/
	restoreProfile() {
		this._rendererProfile.restore();
	}
	/**
	* モデル描画直前のオフスクリーン設定を行う
	*/
	beforeDrawModelRenderTarget() {
		if (this._modelRenderTargets.length == 0) return;
		for (let i = 0; i < this._modelRenderTargets.length; ++i) if (this._modelRenderTargets[i].getBufferWidth() != this._modelRenderTargetWidth || this._modelRenderTargets[i].getBufferHeight() != this._modelRenderTargetHeight) this._modelRenderTargets[i].createRenderTarget(this.gl, this._modelRenderTargetWidth, this._modelRenderTargetHeight, this._currentFbo);
		this._modelRenderTargets[0].beginDraw();
		this._modelRenderTargets[0].clear(0, 0, 0, 0);
	}
	/**
	* モデル描画後のオフスクリーン設定を行う
	*/
	afterDrawModelRenderTarget() {
		if (this._modelRenderTargets.length == 0) return;
		this._modelRenderTargets[0].endDraw();
		CubismShaderManager_WebGL.getInstance().getShader(this.gl).setupShaderProgramForOffscreenRenderTarget(this);
		if (CubismShaderManager_WebGL.getInstance().getShader(this.gl)._isShaderLoaded) {
			const indexBuffer = this.gl.createBuffer();
			this.gl.bindBuffer(this.gl.ELEMENT_ARRAY_BUFFER, indexBuffer);
			this.gl.bufferData(this.gl.ELEMENT_ARRAY_BUFFER, s_renderTargetIndexArray, this.gl.STATIC_DRAW);
			this.gl.drawElements(this.gl.TRIANGLES, s_renderTargetIndexArray.length, this.gl.UNSIGNED_SHORT, 0);
			this.gl.deleteBuffer(indexBuffer);
		}
		this.gl.useProgram(null);
	}
	/**
	* オフスクリーンのクリッピングマスクのバッファを取得する
	*
	* @param index オフスクリーンのクリッピングマスクのバッファのインデックス
	*
	* @return オフスクリーンのクリッピングマスクのバッファへのポインタ
	*/
	getOffscreenMaskBuffer(index) {
		return this._offscreenMasks[index];
	}
	/**
	* レンダラが保持する静的なリソースを解放する
	* WebGLの静的なシェーダープログラムを解放する
	*/
	static doStaticRelease() {
		CubismShaderManager_WebGL.deleteInstance();
	}
	/**
	* レンダーステートを設定する
	*
	* @param fbo アプリケーション側で指定しているフレームバッファ
	* @param viewport ビューポート
	*/
	setRenderState(fbo, viewport) {
		this.gl.bindFramebuffer(this.gl.FRAMEBUFFER, fbo);
		this.gl.viewport(viewport[0], viewport[1], viewport[2], viewport[3]);
		if (this._modelRenderTargetWidth != viewport[2] || this._modelRenderTargetHeight != viewport[3]) {
			this._modelRenderTargetWidth = viewport[2];
			this._modelRenderTargetHeight = viewport[3];
		}
	}
	/**
	* 描画開始時の追加処理
	* モデルを描画する前にクリッピングマスクに必要な処理を実装している
	*/
	preDraw() {
		this.gl.disable(this.gl.SCISSOR_TEST);
		this.gl.disable(this.gl.STENCIL_TEST);
		this.gl.disable(this.gl.DEPTH_TEST);
		this.gl.frontFace(this.gl.CW);
		this.gl.enable(this.gl.BLEND);
		this.gl.colorMask(true, true, true, true);
		this.gl.bindBuffer(this.gl.ARRAY_BUFFER, null);
		this.gl.bindBuffer(this.gl.ELEMENT_ARRAY_BUFFER, null);
		if (this.getAnisotropy() > 0 && this._extension) for (let i = 0; i < this._textures.size; ++i) {
			this.gl.bindTexture(this.gl.TEXTURE_2D, this._textures.get(i));
			this.gl.texParameterf(this.gl.TEXTURE_2D, this._extension.TEXTURE_MAX_ANISOTROPY_EXT, this.getAnisotropy());
		}
	}
	/**
	* Drawableのマスク用のオフスクリーンサーフェースを取得する
	*
	* @param index オフスクリーンサーフェースのインデックス
	*
	* @return マスク用のオフスクリーンサーフェース
	*/
	getDrawableMaskBuffer(index) {
		return this._drawableMasks[index];
	}
	/**
	* マスクテクスチャに描画するクリッピングコンテキストをセットする
	*/
	setClippingContextBufferForMask(clip) {
		this._clippingContextBufferForMask = clip;
	}
	/**
	* マスクテクスチャに描画するクリッピングコンテキストを取得する
	*
	* @return マスクテクスチャに描画するクリッピングコンテキスト
	*/
	getClippingContextBufferForMask() {
		return this._clippingContextBufferForMask;
	}
	/**
	* Drawableの画面上に描画するクリッピングコンテキストをセットする
	*
	* @param clip drawableで画面上に描画するクリッピングコンテキスト
	*/
	setClippingContextBufferForDrawable(clip) {
		this._clippingContextBufferForDraw = clip;
	}
	/**
	* Drawableの画面上に描画するクリッピングコンテキストを取得する
	*
	* @return Drawableの画面上に描画するクリッピングコンテキスト
	*/
	getClippingContextBufferForDrawable() {
		return this._clippingContextBufferForDraw;
	}
	/**
	* offscreenで画面上に描画するクリッピングコンテキストをセットする。
	*
	* @param clip offscreenで画面上に描画するクリッピングコンテキスト
	*/
	setClippingContextBufferForOffscreen(clip) {
		this._clippingContextBufferForOffscreen = clip;
	}
	/**
	* offscreenで画面上に描画するクリッピングコンテキストを取得する。
	*
	* @return offscreenで画面上に描画するクリッピングコンテキスト
	*/
	getClippingContextBufferForOffscreen() {
		return this._clippingContextBufferForOffscreen;
	}
	/**
	* マスク生成時かを判定する
	*
	* @return 判定値
	*/
	isGeneratingMask() {
		return this.getClippingContextBufferForMask() != null;
	}
	/**
	* glの設定
	*/
	startUp(gl) {
		this.gl = gl;
		if (this._drawableClippingManager) this._drawableClippingManager.setGL(gl);
		if (this._offscreenClippingManager) this._offscreenClippingManager.setGL(gl);
		CubismShaderManager_WebGL.getInstance().setGlContext(gl);
		this._rendererProfile.setGl(gl);
		this._extension = this.gl.getExtension("EXT_texture_filter_anisotropic") || this.gl.getExtension("WEBKIT_EXT_texture_filter_anisotropic") || this.gl.getExtension("MOZ_EXT_texture_filter_anisotropic");
		if (this._model.isUsingMasking()) {
			this._drawableMasks.length = this._drawableClippingManager.getRenderTextureCount();
			for (let i = 0; i < this._drawableMasks.length; ++i) {
				const renderTarget = new CubismRenderTarget_WebGL();
				renderTarget.createRenderTarget(this.gl, this._drawableClippingManager.getClippingMaskBufferSize(), this._drawableClippingManager.getClippingMaskBufferSize(), this._currentFbo);
				this._drawableMasks[i] = renderTarget;
			}
		}
		if (this._model.isBlendModeEnabled()) {
			this._modelRenderTargets.length = 0;
			const createSize = 3;
			this._modelRenderTargets.length = createSize;
			for (let i = 0; i < createSize; ++i) {
				const offscreenRenderTarget = new CubismOffscreenRenderTarget_WebGL();
				offscreenRenderTarget.createRenderTarget(this.gl, this._modelRenderTargetWidth, this._modelRenderTargetHeight, this._currentFbo);
				this._modelRenderTargets[i] = offscreenRenderTarget;
			}
			if (this._model.isUsingMaskingForOffscreen()) {
				this._offscreenMasks.length = this._offscreenClippingManager.getRenderTextureCount();
				for (let i = 0; i < this._offscreenMasks.length; ++i) {
					const offscreenMask = new CubismRenderTarget_WebGL();
					offscreenMask.createRenderTarget(this.gl, this._offscreenClippingManager.getClippingMaskBufferSize(), this._offscreenClippingManager.getClippingMaskBufferSize(), this._currentFbo);
					this._offscreenMasks[i] = offscreenMask;
				}
			}
			const offscreenCount = this._model.getOffscreenCount();
			if (offscreenCount > 0) {
				this._offscreenList = new Array(offscreenCount);
				for (let offscreenIndex = 0; offscreenIndex < offscreenCount; ++offscreenIndex) {
					const offscreenRenderTarget = new CubismOffscreenRenderTarget_WebGL();
					offscreenRenderTarget.setOffscreenIndex(offscreenIndex);
					this._offscreenList[offscreenIndex] = offscreenRenderTarget;
				}
				this.setupParentOffscreens(this._model, offscreenCount);
			}
		}
		this.gl.bindFramebuffer(this.gl.FRAMEBUFFER, this._currentFbo);
	}
};
/**
* レンダラが保持する静的なリソースを開放する
*/
CubismRenderer.staticRelease = () => {
	CubismRenderer_WebGL.doStaticRelease();
};
var Live2DCubismFramework$4;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismClippingContext = CubismClippingContext_WebGL;
	_Live2DCubismFramework.CubismClippingManager_WebGL = CubismClippingManager_WebGL;
	_Live2DCubismFramework.CubismRenderer_WebGL = CubismRenderer_WebGL;
})(Live2DCubismFramework$4 || (Live2DCubismFramework$4 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/model/cubismmoc.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
/**
* Mocデータの管理
*
* Mocデータの管理を行うクラス。
*/
var CubismMoc = class CubismMoc {
	/**
	* Mocデータの作成
	*/
	static create(mocBytes, shouldCheckMocConsistency) {
		let cubismMoc = null;
		if (shouldCheckMocConsistency) {
			if (!this.hasMocConsistency(mocBytes)) {
				CubismLogError(`Inconsistent MOC3.`);
				return cubismMoc;
			}
		}
		const moc = Live2DCubismCore.Moc.fromArrayBuffer(mocBytes);
		if (moc) {
			cubismMoc = new CubismMoc(moc);
			cubismMoc._mocVersion = Live2DCubismCore.Version.csmGetMocVersion(mocBytes);
		}
		return cubismMoc;
	}
	/**
	* Mocデータを削除
	*
	* Mocデータを削除する
	*/
	static delete(moc) {
		moc._moc._release();
		moc._moc = null;
		moc = null;
	}
	/**
	* モデルを作成する
	*
	* @return Mocデータから作成されたモデル
	*/
	createModel() {
		let cubismModel = null;
		const model = Live2DCubismCore.Model.fromMoc(this._moc);
		if (model) {
			cubismModel = new CubismModel(model);
			cubismModel.initialize();
			++this._modelCount;
		}
		return cubismModel;
	}
	/**
	* モデルを削除する
	*/
	deleteModel(model) {
		if (model != null) {
			model.release();
			model = null;
			--this._modelCount;
		}
	}
	/**
	* コンストラクタ
	*/
	constructor(moc) {
		this._moc = moc;
		this._modelCount = 0;
		this._mocVersion = 0;
	}
	/**
	* デストラクタ相当の処理
	*/
	release() {
		CSM_ASSERT(this._modelCount == 0);
		this._moc._release();
		this._moc = null;
	}
	/**
	* 最新の.moc3 Versionを取得
	*/
	getLatestMocVersion() {
		return Live2DCubismCore.Version.csmGetLatestMocVersion();
	}
	/**
	* 読み込んだモデルの.moc3 Versionを取得
	*/
	getMocVersion() {
		return this._mocVersion;
	}
	/**
	* Mocファイルのbufferから.moc3 Versionを取得
	* @param mocBytes Mocファイルのバイト配列
	* @returns .moc3 Version番号
	*/
	static getMocVersionFromBuffer(mocBytes) {
		return Live2DCubismCore.Version.csmGetMocVersion(mocBytes);
	}
	/**
	* .moc3 の整合性を検証する
	*/
	static hasMocConsistency(mocBytes) {
		return Live2DCubismCore.Moc.prototype.hasMocConsistency(mocBytes) === 1 ? true : false;
	}
};
var Live2DCubismFramework$3;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismMoc = CubismMoc;
})(Live2DCubismFramework$3 || (Live2DCubismFramework$3 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/model/cubismmodeluserdatajson.ts
var Meta = "Meta";
var UserDataCount = "UserDataCount";
var TotalUserDataSize = "TotalUserDataSize";
var UserData = "UserData";
var Target = "Target";
var Id = "Id";
var Value = "Value";
var CubismModelUserDataJson = class {
	/**
	* コンストラクタ
	* @param buffer    userdata3.jsonが読み込まれているバッファ
	* @param size      バッファのサイズ
	*/
	constructor(buffer, size) {
		this._json = CubismJson.create(buffer, size);
	}
	/**
	* デストラクタ相当の処理
	*/
	release() {
		CubismJson.delete(this._json);
	}
	/**
	* ユーザーデータ個数の取得
	* @return ユーザーデータの個数
	*/
	getUserDataCount() {
		return this._json.getRoot().getValueByString(Meta).getValueByString(UserDataCount).toInt();
	}
	/**
	* ユーザーデータ総文字列数の取得
	*
	* @return ユーザーデータ総文字列数
	*/
	getTotalUserDataSize() {
		return this._json.getRoot().getValueByString(Meta).getValueByString(TotalUserDataSize).toInt();
	}
	/**
	* ユーザーデータのタイプの取得
	*
	* @return ユーザーデータのタイプ
	*/
	getUserDataTargetType(i) {
		return this._json.getRoot().getValueByString(UserData).getValueByIndex(i).getValueByString(Target).getRawString();
	}
	/**
	* ユーザーデータのターゲットIDの取得
	*
	* @param i インデックス
	* @return ユーザーデータターゲットID
	*/
	getUserDataId(i) {
		return CubismFramework.getIdManager().getId(this._json.getRoot().getValueByString(UserData).getValueByIndex(i).getValueByString(Id).getRawString());
	}
	/**
	* ユーザーデータの文字列の取得
	*
	* @param i インデックス
	* @return ユーザーデータ
	*/
	getUserDataValue(i) {
		return this._json.getRoot().getValueByString(UserData).getValueByIndex(i).getValueByString(Value).getRawString();
	}
};
var Live2DCubismFramework$2;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismModelUserDataJson = CubismModelUserDataJson;
})(Live2DCubismFramework$2 || (Live2DCubismFramework$2 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/model/cubismmodeluserdata.ts
var ArtMesh = "ArtMesh";
/**
* ユーザーデータインターフェース
*
* Jsonから読み込んだユーザーデータを記録しておくための構造体
*/
var CubismModelUserDataNode = class {};
/**
* ユーザデータの管理クラス
*
* ユーザデータをロード、管理、検索インターフェイス、解放までを行う。
*/
var CubismModelUserData = class CubismModelUserData {
	/**
	* インスタンスの作成
	*
	* @param buffer    userdata3.jsonが読み込まれているバッファ
	* @param size      バッファのサイズ
	* @return 作成されたインスタンス
	*/
	static create(buffer, size) {
		const ret = new CubismModelUserData();
		ret.parseUserData(buffer, size);
		return ret;
	}
	/**
	* インスタンスを破棄する
	*
	* @param modelUserData 破棄するインスタンス
	*/
	static delete(modelUserData) {
		if (modelUserData != null) {
			modelUserData.release();
			modelUserData = null;
		}
	}
	/**
	* ArtMeshのユーザーデータのリストの取得
	*
	* @return ユーザーデータリスト
	*/
	getArtMeshUserDatas() {
		return this._artMeshUserDataNode;
	}
	/**
	* userdata3.jsonのパース
	*
	* @param buffer    userdata3.jsonが読み込まれているバッファ
	* @param size      バッファのサイズ
	*/
	parseUserData(buffer, size) {
		let json = new CubismModelUserDataJson(buffer, size);
		if (!json) {
			json.release();
			json = void 0;
			return;
		}
		const typeOfArtMesh = CubismFramework.getIdManager().getId(ArtMesh);
		const nodeCount = json.getUserDataCount();
		let dstIndex = this._userDataNodes.length;
		this._userDataNodes.length = nodeCount;
		for (let i = 0; i < nodeCount; i++) {
			const addNode = new CubismModelUserDataNode();
			addNode.targetId = json.getUserDataId(i);
			addNode.targetType = CubismFramework.getIdManager().getId(json.getUserDataTargetType(i));
			addNode.value = json.getUserDataValue(i);
			this._userDataNodes[dstIndex++] = addNode;
			if (addNode.targetType == typeOfArtMesh) this._artMeshUserDataNode.push(addNode);
		}
		json.release();
		json = void 0;
	}
	/**
	* コンストラクタ
	*/
	constructor() {
		this._userDataNodes = new Array();
		this._artMeshUserDataNode = new Array();
	}
	/**
	* デストラクタ相当の処理
	*
	* ユーザーデータ構造体配列を解放する
	*/
	release() {
		for (let i = 0; i < this._userDataNodes.length; ++i) this._userDataNodes[i] = null;
		this._userDataNodes = null;
	}
};
var Live2DCubismFramework$1;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismModelUserData = CubismModelUserData;
	_Live2DCubismFramework.CubismModelUserDataNode = CubismModelUserDataNode;
})(Live2DCubismFramework$1 || (Live2DCubismFramework$1 = {}));
// examples/packages/live2d_pet/vendor/Framework/src/model/cubismusermodel.ts
/**
* Copyright(c) Live2D Inc. All rights reserved.
*
* Use of this source code is governed by the Live2D Open Software license
* that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
*/
/**
* ユーザーが実際に使用するモデル
*
* ユーザーが実際に使用するモデルの基底クラス。これを継承してユーザーが実装する。
*/
var CubismUserModel = class CubismUserModel {
	/**
	* 初期化状態の取得
	*
	* 初期化されている状態か？
	*
	* @return true     初期化されている
	* @return false    初期化されていない
	*/
	isInitialized() {
		return this._initialized;
	}
	/**
	* 初期化状態の設定
	*
	* 初期化状態を設定する。
	*
	* @param v 初期化状態
	*/
	setInitialized(v) {
		this._initialized = v;
	}
	/**
	* 更新状態の取得
	*
	* 更新されている状態か？
	*
	* @return true     更新されている
	* @return false    更新されていない
	*/
	isUpdating() {
		return this._updating;
	}
	/**
	* 更新状態の設定
	*
	* 更新状態を設定する
	*
	* @param v 更新状態
	*/
	setUpdating(v) {
		this._updating = v;
	}
	/**
	* マウスドラッグ情報の設定
	*
	* @param ドラッグしているカーソルのX位置
	* @param ドラッグしているカーソルのY位置
	*/
	setDragging(x, y) {
		this._dragManager.set(x, y);
	}
	/**
	* モデル行列を取得する
	* @return モデル行列
	*/
	getModelMatrix() {
		return this._modelMatrix;
	}
	/**
	* モデルを描画したバッファを設定する
	*
	* @param width モデルを描画したバッファの幅
	* @param height モデルを描画したバッファの高さ
	*/
	setRenderTargetSize(width, height) {
		if (this._renderer) this._renderer.setRenderTargetSize(width, height);
	}
	/**
	* 不透明度の設定
	*
	* @param a 不透明度
	*/
	setOpacity(a) {
		this._opacity = a;
	}
	/**
	* 不透明度の取得
	*
	* @return 不透明度
	*/
	getOpacity() {
		return this._opacity;
	}
	/**
	* モデルデータを読み込む
	*
	* @param buffer    moc3ファイルが読み込まれているバッファ
	*/
	loadModel(buffer, shouldCheckMocConsistency = false) {
		this._moc = CubismMoc.create(buffer, shouldCheckMocConsistency);
		if (this._moc == null) {
			CubismLogError("Failed to CubismMoc.create().");
			return;
		}
		this._model = this._moc.createModel();
		if (this._model == null) {
			CubismLogError("Failed to CreateModel().");
			return;
		}
		this._model.saveParameters();
		this._modelMatrix = new CubismModelMatrix(this._model.getCanvasWidth(), this._model.getCanvasHeight());
	}
	/**
	* モーションデータを読み込む
	* @param buffer motion3.jsonファイルが読み込まれているバッファ
	* @param size バッファのサイズ
	* @param name モーションの名前
	* @param onFinishedMotionHandler モーション再生終了時に呼び出されるコールバック関数
	* @param onBeganMotionHandler モーション再生開始時に呼び出されるコールバック関数
	* @param modelSetting モデル設定
	* @param group モーショングループ名
	* @param index モーションインデックス
	* @param shouldCheckMotionConsistency motion3.json整合性チェックするかどうか
	* @return モーションクラス
	*/
	loadMotion(buffer, size, name, onFinishedMotionHandler, onBeganMotionHandler, modelSetting, group, index, shouldCheckMotionConsistency = false) {
		if (buffer == null || size == 0) {
			CubismLogError("Failed to loadMotion().");
			return null;
		}
		const motion = CubismMotion.create(buffer, size, onFinishedMotionHandler, onBeganMotionHandler, shouldCheckMotionConsistency);
		if (motion == null) {
			CubismLogError(`Failed to create motion from buffer in LoadMotion()`);
			return null;
		}
		if (modelSetting) {
			const fadeInTime = modelSetting.getMotionFadeInTimeValue(group, index);
			if (fadeInTime >= 0) motion.setFadeInTime(fadeInTime);
			const fadeOutTime = modelSetting.getMotionFadeOutTimeValue(group, index);
			if (fadeOutTime >= 0) motion.setFadeOutTime(fadeOutTime);
		}
		return motion;
	}
	/**
	* 表情データの読み込み
	* @param buffer expファイルが読み込まれているバッファ
	* @param size バッファのサイズ
	* @param name 表情の名前
	*/
	loadExpression(buffer, size, name) {
		if (buffer == null || size == 0) {
			CubismLogError("Failed to loadExpression().");
			return null;
		}
		return CubismExpressionMotion.create(buffer, size);
	}
	/**
	* ポーズデータの読み込み
	* @param buffer pose3.jsonが読み込まれているバッファ
	* @param size バッファのサイズ
	*/
	loadPose(buffer, size) {
		if (buffer == null || size == 0) {
			CubismLogError("Failed to loadPose().");
			return;
		}
		this._pose = CubismPose.create(buffer, size);
	}
	/**
	* モデルに付属するユーザーデータを読み込む
	* @param buffer userdata3.jsonが読み込まれているバッファ
	* @param size バッファのサイズ
	*/
	loadUserData(buffer, size) {
		if (buffer == null || size == 0) {
			CubismLogError("Failed to loadUserData().");
			return;
		}
		this._modelUserData = CubismModelUserData.create(buffer, size);
	}
	/**
	* 物理演算データの読み込み
	* @param buffer  physics3.jsonが読み込まれているバッファ
	* @param size    バッファのサイズ
	*/
	loadPhysics(buffer, size) {
		if (buffer == null || size == 0) {
			CubismLogError("Failed to loadPhysics().");
			return;
		}
		this._physics = CubismPhysics.create(buffer, size);
	}
	/**
	* 当たり判定の取得
	* @param drawableId 検証したいDrawableのID
	* @param pointX X位置
	* @param pointY Y位置
	* @return true ヒットしている
	* @return false ヒットしていない
	*/
	isHit(drawableId, pointX, pointY) {
		const drawIndex = this._model.getDrawableIndex(drawableId);
		if (drawIndex < 0) return false;
		const count = this._model.getDrawableVertexCount(drawIndex);
		const vertices = this._model.getDrawableVertices(drawIndex);
		let left = vertices[0];
		let right = vertices[0];
		let top = vertices[1];
		let bottom = vertices[1];
		for (let j = 1; j < count; ++j) {
			const x = vertices[Constant.vertexOffset + j * Constant.vertexStep];
			const y = vertices[Constant.vertexOffset + j * Constant.vertexStep + 1];
			if (x < left) left = x;
			if (x > right) right = x;
			if (y < top) top = y;
			if (y > bottom) bottom = y;
		}
		const tx = this._modelMatrix.invertTransformX(pointX);
		const ty = this._modelMatrix.invertTransformY(pointY);
		return left <= tx && tx <= right && top <= ty && ty <= bottom;
	}
	/**
	* モデルの取得
	* @return モデル
	*/
	getModel() {
		return this._model;
	}
	/**
	* 読み込めないMocファイルの.moc3 Versionを取得
	* @param mocBytes 読み込めないMocファイルのバイト配列
	* @returns .moc3 Version番号
	*/
	getMocVersionFromBuffer(mocBytes) {
		return CubismMoc.getMocVersionFromBuffer(mocBytes);
	}
	/**
	* レンダラの取得
	* @return レンダラ
	*/
	getRenderer() {
		return this._renderer;
	}
	/**
	* レンダラを作成して初期化を実行する
	* @param width レンダリングする幅
	* @param height レンダリングする高さ
	* @param maskBufferCount バッファの生成数
	*/
	createRenderer(width, height, maskBufferCount = 1) {
		if (this._renderer) this.deleteRenderer();
		this._renderer = new CubismRenderer_WebGL(width, height);
		this._renderer.initialize(this._model, maskBufferCount);
	}
	/**
	* レンダラの解放
	*/
	deleteRenderer() {
		if (this._renderer != null) {
			this._renderer.release();
			this._renderer = null;
		}
	}
	/**
	* イベント発火時の標準処理
	*
	* Eventが再生処理時にあった場合の処理をする。
	* 継承で上書きすることを想定している。
	* 上書きしない場合はログ出力をする。
	*
	* @param eventValue 発火したイベントの文字列データ
	*/
	motionEventFired(eventValue) {
		CubismLogInfo("{0}", eventValue);
	}
	/**
	* イベント用のコールバック
	*
	* CubismMotionQueueManagerにイベント用に登録するためのCallback。
	* CubismUserModelの継承先のEventFiredを呼ぶ。
	*
	* @param caller 発火したイベントを管理していたモーションマネージャー、比較用
	* @param eventValue 発火したイベントの文字列データ
	* @param customData CubismUserModelを継承したインスタンスを想定
	*/
	static cubismDefaultMotionEventCallback(caller, eventValue, customData) {
		const model = customData;
		if (model != null) model.motionEventFired(eventValue);
	}
	/**
	* コンストラクタ
	*/
	constructor() {
		this._moc = null;
		this._model = null;
		this._motionManager = null;
		this._expressionManager = null;
		this._eyeBlink = null;
		this._breath = null;
		this._modelMatrix = null;
		this._pose = null;
		this._dragManager = null;
		this._physics = null;
		this._modelUserData = null;
		this._initialized = false;
		this._updating = false;
		this._opacity = 1;
		this._mocConsistency = false;
		this._debugMode = false;
		this._renderer = null;
		this._motionManager = new CubismMotionManager();
		this._motionManager.setEventCallback(CubismUserModel.cubismDefaultMotionEventCallback, this);
		this._expressionManager = new CubismExpressionMotionManager();
		this._dragManager = new CubismTargetPoint();
	}
	/**
	* デストラクタに相当する処理
	*/
	release() {
		if (this._motionManager != null) {
			this._motionManager.release();
			this._motionManager = null;
		}
		if (this._expressionManager != null) {
			this._expressionManager.release();
			this._expressionManager = null;
		}
		if (this._moc != null) {
			this._moc.deleteModel(this._model);
			this._moc.release();
			this._moc = null;
		}
		this._modelMatrix = null;
		CubismPose.delete(this._pose);
		CubismEyeBlink.delete(this._eyeBlink);
		CubismBreath.delete(this._breath);
		this._dragManager = null;
		CubismPhysics.delete(this._physics);
		CubismModelUserData.delete(this._modelUserData);
		this.deleteRenderer();
	}
};
var Live2DCubismFramework;
(function(_Live2DCubismFramework) {
	_Live2DCubismFramework.CubismUserModel = CubismUserModel;
})(Live2DCubismFramework || (Live2DCubismFramework = {}));
// examples/packages/live2d_pet/src/performance.js
var FrameStats = class {
	constructor() {
		this.reset();
	}
	reset() {
		this.samples = [];
	}
	add(time, cost) {
		this.samples.push({
			time,
			cost
		});
		while (this.samples.length && this.samples[0].time < time - 1e4) this.samples.shift();
	}
	summary() {
		const samples = this.samples;
		const costs = samples.map((s) => s.cost).sort((a, b) => a - b);
		const duration = samples.length > 1 ? samples.at(-1).time - samples[0].time : 0;
		return {
			seconds: duration / 1e3,
			frames: samples.length,
			fps: duration ? (samples.length - 1) * 1e3 / duration : 0,
			cpuMean: costs.length ? costs.reduce((a, b) => a + b, 0) / costs.length : 0,
			cpuP95: costs[Math.max(0, Math.ceil(costs.length * .95) - 1)] || 0,
			stalls: samples.slice(1).filter((s, i) => s.time - samples[i].time > 100).length
		};
	}
};
// examples/packages/live2d_pet/ui/models/dragon.js
var DRAGON_EXPRESSIONS = {
	抬手: "侧举折扇",
	抬手2: "持扇掩面",
	开扇: "合起折扇",
	扇面: "切换扇面",
	变: "幼年态（小个子）"
};
var dragon = {
	configurePreview() {},
	framingPan: 0,
	id: "dragon",
	name: "龙娘",
	bundled: false,
	size: [500, 671],
	fps: 60,
	releaseDuration: 800,
	idleChoices: {
		gentle: "轻柔待机",
		wings: "舒展翅膀",
		sleep: "闭眼打盹"
	},
	expressions: DRAGON_EXPRESSIONS,
	appearanceStates: ["变"],
	expressionGroups: [["抬手", "抬手2"]],
	interaction: {
		key: "kt-live2d-dragon-interaction-v1",
		expressions: Object.keys(DRAGON_EXPRESSIONS),
		cycle: ["抬手", "抬手2"],
		exclusiveCycle: true,
		sleep: true
	},
	aliases: {
		ParamAngleX: "ParamAngleX1",
		ParamAngleY: "ParamAngleY1",
		ParamAngleZ: "ParamAngleZ1"
	},
	headParts: [
		"Part22",
		"Part11",
		"Part14",
		"Part20",
		"Part21",
		"Part15",
		"Part16"
	],
	mouth: "ParamMouthOpenY",
	boundsParameter: "Param76",
	workMotions: {},
	exitParameters: [
		"Param36",
		"Param78",
		"Param80"
	],
	nod: {
		ParamAngleY: [
			0,
			4,
			-28,
			5,
			-20,
			0
		],
		ParamBodyAngleY: [
			0,
			0,
			-5,
			0,
			-3,
			0
		]
	},
	normalizeExpressions(names) {
		if (names.some((n) => ["开扇", "扇面"].includes(n)) && !names.some((n) => ["抬手", "抬手2"].includes(n))) names.unshift("抬手");
		return names;
	},
	toggleExtras(active, name, removing) {
		if (removing && ["抬手", "抬手2"].includes(name)) {
			active.delete("开扇");
			active.delete("扇面");
		}
		if (name === "扇面") active.delete("开扇");
	},
	workPose({ state, t, action, idleStyle, exitFrom }) {
		let target;
		target = {
			Param76: -7 + Math.sin(t * 1.1),
			Param77: Math.sin(t * 1.1)
		};
		if (state === "idle" && idleStyle === "wings") target = {};
		if (state === "running") target = {
			Param76: 1 + 8 * Math.sin(t * 2.4),
			Param77: 6 * Math.cos(t * 2.4),
			ParamBodyAngleZ: 2 * Math.sin(t * 1.2)
		};
		else if (state === "waiting") Object.assign(target, {
			Param78: 1,
			Param80: 0,
			Param36: 0
		});
		else if (state === "resolving") {
			const seconds = (action.progress || 0) * .8;
			Object.assign(target, {
				Param36: exitFrom.Param36 + (10 - exitFrom.Param36) * Math.min(1, seconds / .35),
				Param78: exitFrom.Param78 * (1 - Math.max(0, Math.min(1, (seconds - .35) / .45))),
				Param80: 0
			});
		} else if (state === "ready") {
			const lift = Math.sin(Math.PI * Math.min(1, (action.progress || 0) / .85));
			Object.assign(target, {
				Param76: 10,
				Param77: 8 * lift,
				ParamBodyAngleY: -3 * lift
			});
		}
		return target;
	}
};
// examples/packages/live2d_pet/ui/models/deepseek.js
var WORK_MOTIONS = {
	FingerIn: "Animation/竖手指切出来动画.motion3.json",
	FingerOut: "Animation/竖手指切回默认.motion3.json",
	RaiseIn: "Animation/默认手切举手.motion3.json",
	RaiseOut: "Animation/举手切默认手.motion3.json"
};
var fishExpressions = [
	"脸红",
	"流泪",
	"爱心眼",
	"圈圈眼",
	"屑屑眼",
	"＞＜",
	"流冷汗",
	"黑脸",
	"加载",
	"兔牙",
	"单片眼镜",
	"黑框眼镜",
	"钢盆",
	"体型1",
	"体型2",
	"默认体型",
	"前倾",
	"尾巴开关",
	"裙子切换",
	"脱鞋",
	"胸口衣服"
];
var fishGroups = [
	[
		"爱心眼",
		"圈圈眼",
		"屑屑眼",
		"＞＜"
	],
	[
		"体型1",
		"体型2",
		"默认体型"
	],
	["单片眼镜", "黑框眼镜"]
];
var fishInteraction = {
	key: "kt-live2d-deepseek-interaction-v1",
	expressions: fishExpressions,
	cycle: ["脸红", "爱心眼"],
	exclusiveCycle: false,
	sleep: false
};
// examples/packages/live2d_pet/ui/models/index.js
var models = {
	dragon,
	deepseek: {
		configurePreview(quality) {
			document.title = "大肥鱼 · Live2D 画质比较";
			document.querySelector("h1").replaceChildren("大肥鱼的小天地");
			const subtitle = document.createElement("span");
			subtitle.textContent = quality === "high" ? "高清档 · 最大边 4096" : "日常档 · 最大边 2048";
			document.querySelector("h1").append(subtitle);
			document.querySelector(".viewport").setAttribute("aria-label", "大肥鱼模型预览");
			document.querySelector("#model").setAttribute("aria-label", "可互动的大肥鱼 Live2D 模型");
			document.querySelector("aside header .eyebrow").textContent = "DEEPSEEK COMPANION";
			document.querySelector("aside header p").textContent = "放大后切换画质，比较眼睛、发丝与衣服细线。";
			document.querySelector("[data-motion=Idle]").textContent = "自然待机";
			document.querySelector("[data-motion=Sleep]").remove();
			const buttons = document.querySelector(".expressions");
			buttons.replaceChildren();
			for (const name of [...fishExpressions, ""]) {
				const button = document.createElement("button");
				button.dataset.expression = name;
				button.textContent = name || "恢复表情";
				buttons.append(button);
			}
			document.querySelector(".note").textContent = "再点一次关闭。眼型、眼镜和体型分别互斥；其他表情可叠加。";
			document.querySelector("#pet-menu-button").setAttribute("aria-label", "大肥鱼动作菜单");
			document.querySelector("#pet-menu").setAttribute("aria-label", "大肥鱼动作");
			document.querySelector("#pet-menu strong").textContent = "大肥鱼表情";
			document.querySelector("#pet-menu p").textContent = "摸头或点击身体会回应；右键选择固定表情。";
			document.querySelector("#credits").textContent = "大肥鱼 · 原画及拆分：神宫凉子 · Live2D：霓虹猫_NeonNeko · 神宫凉子免费配布模型";
		},
		framingPan: -1,
		id: "deepseek",
		name: "大肥鱼 DeepSeek",
		shortName: "大肥鱼",
		bundled: false,
		size: [384, 516],
		fps: 30,
		releaseDuration: 600,
		idleChoices: { natural: "自然待机" },
		expressions: Object.fromEntries(fishExpressions.map((name) => [name, name])),
		appearanceStates: [
			"兔牙",
			"单片眼镜",
			"黑框眼镜",
			"钢盆",
			"体型1",
			"体型2",
			"默认体型",
			"尾巴开关",
			"裙子切换",
			"脱鞋",
			"胸口衣服"
		],
		expressionGroups: fishGroups,
		interaction: fishInteraction,
		aliases: {},
		headParts: [
			"Part15",
			"Part154",
			"Part2",
			"Part9",
			"Part111",
			"Part34",
			"Part155"
		],
		mouth: "ParamMouthOpenY",
		boundsParameter: null,
		workMotions: WORK_MOTIONS,
		exitParameters: ["LeanInput"],
		nod: {
			ParamAngleY: [
				0,
				3,
				-20,
				4,
				-14,
				0
			],
			ParamBodyAngleY: [
				0,
				0,
				-2,
				0,
				-1,
				0
			]
		},
		normalizeExpressions: (names) => names,
		toggleExtras() {},
		workPose({ state, t, action, previous, exitFrom, model, sample }) {
			let target = {};
			if (state === "running") {
				target = sample("FingerIn", t);
				target.ParamBodyAngleZ = 5 * Math.sin(t * 1.6);
			} else if (state === "waiting") target.LeanInput = -30;
			else if (state === "resolving") {
				const progress = Math.max(0, Math.min(1, action.progress || 0));
				const ease = progress * progress * (3 - 2 * progress);
				const index = model.getModel().parameters.ids.indexOf("LeanInput");
				target.LeanInput = exitFrom.LeanInput + (model.getParameterValueByIndex(index) - exitFrom.LeanInput) * ease;
			} else if (state === "ready") {
				const seconds = (action.progress || 0) * 3.4;
				target = seconds < 2.3 ? sample("RaiseIn", seconds) : sample("RaiseOut", seconds - 2.3);
			}
			if (state !== "running" && previous === "running" && t < 1.25) target = {
				...sample("FingerOut", t),
				...target
			};
			return target;
		}
	}
};
function modelAdapter(id = "dragon") {
	const adapter = models[id];
	if (!adapter) throw new Error(`Unsupported Live2D adapter: ${id}`);
	return adapter;
}
function normalizeExpressions(adapter, names) {
	let result = [...new Set(names.filter((name) => Object.hasOwn(adapter.expressions, name)))];
	for (const group of adapter.expressionGroups) {
		const last = result.filter((name) => group.includes(name)).at(-1);
		result = result.filter((name) => !group.includes(name) || name === last);
	}
	return adapter.normalizeExpressions(result);
}
function toggleExpression(adapter, names, name) {
	if (!name) return [];
	if (!Object.hasOwn(adapter.expressions, name)) return [...names];
	const active = new Set(names);
	const removing = active.delete(name);
	if (!removing) {
		for (const group of adapter.expressionGroups) if (group.includes(name)) for (const other of group) active.delete(other);
		active.add(name);
	}
	adapter.toggleExtras(active, name, removing);
	return normalizeExpressions(adapter, [...active]);
}
// examples/packages/live2d_pet/src/work-actions.js
var WorkActions = class {
	constructor(profile) {
		this.profile = profile;
		this.adapter = modelAdapter(profile);
		this.clips = /* @__PURE__ */ new Map();
		this.pose = /* @__PURE__ */ new Map();
		this.state = "idle";
		this.elapsed = 0;
		this.suspended = 0;
		this.settling = 0;
	}
	load(owner, name, data) {
		const definition = JSON.parse(new TextDecoder().decode(data));
		const motion = owner.loadMotion(data, data.byteLength, name);
		motion.setLoop(false);
		motion.setFadeInTime(0);
		motion.setFadeOutTime(0);
		const ids = owner.getModel().getModel().parameters.ids;
		const parameters = definition.Curves.filter((curve) => curve.Target === "Parameter").map((curve) => ({
			name: curve.Id,
			index: ids.indexOf(curve.Id)
		})).filter((parameter) => parameter.index >= 0);
		const entry = new CubismMotionQueueEntry();
		entry.setIsStarted(true);
		entry.setStartTime(0);
		entry.setFadeInStartTime(0);
		this.clips.set(name, {
			motion,
			parameters,
			entry,
			duration: definition.Meta.Duration
		});
	}
	sampleClip(model, name, seconds) {
		const clip = this.clips.get(name);
		if (!clip) return {};
		const original = clip.parameters.map(({ index }) => model.getParameterValueByIndex(index));
		clip.entry.setIsFinished(false);
		clip.motion.doUpdateParameters(model, Math.min(seconds, clip.duration), 1, clip.entry);
		const result = {};
		clip.parameters.forEach(({ name, index }, i) => {
			result[name] = model.getParameterValueByIndex(index);
			model.setParameterValueByIndex(index, original[i]);
		});
		return result;
	}
	get transitioning() {
		if (this.sleeping) return false;
		return this.elapsed < 1.5 || this.state === "ready" || this.suspended > 0 || this.settling > 0;
	}
	refreshBounds() {
		this.settling = 1.5;
	}
	suspend() {
		this.suspended = 2;
	}
	apply(model, dt, action = { state: "idle" }, sleeping = false) {
		if (sleeping) {
			this.pose.clear();
			this.state = "idle";
			this.elapsed = 0;
			this.sleeping = true;
			return;
		}
		if (this.sleeping) {
			this.sleeping = false;
			this.previous = null;
		}
		this.settling = Math.max(0, this.settling - dt);
		const wasSuspended = this.suspended > 0;
		this.suspended = Math.max(0, this.suspended - dt);
		if (wasSuspended && !this.suspended) this.refreshBounds();
		const state = action.state || "idle";
		if (state === "resolving" && this.state !== "resolving") {
			this.exitFrom = {};
			const ids = model.getModel().parameters.ids;
			for (const name of this.adapter.exitParameters) {
				const index = ids.indexOf(name);
				if (index < 0) continue;
				const base = model.getParameterValueByIndex(index);
				const pose = this.pose.get(name);
				this.exitFrom[name] = pose ? base + (pose.value - base) * pose.weight : base;
			}
		}
		if (state !== this.state) {
			this.previous = this.state;
			this.state = state;
			this.elapsed = 0;
		}
		this.elapsed += dt;
		const target = this.suspended ? {} : this.adapter.workPose({
			state,
			t: this.elapsed,
			action,
			idleStyle: this.idleStyle,
			previous: this.previous,
			exitFrom: this.exitFrom,
			model,
			sample: (name, seconds) => this.sampleClip(model, name, seconds)
		});
		const ids = model.getModel().parameters.ids;
		const blend = 1 - Math.exp(-dt / .16);
		for (const name of /* @__PURE__ */ new Set([...this.pose.keys(), ...Object.keys(target)])) {
			const index = ids.indexOf(name);
			if (index < 0) continue;
			const current = this.pose.get(name) || {
				value: model.getParameterValueByIndex(index),
				weight: 0
			};
			const active = Object.hasOwn(target, name);
			if (state === "resolving" && active && this.adapter.exitParameters.includes(name)) {
				current.value = target[name];
				current.weight = 1;
			} else {
				current.value += ((active ? target[name] : current.value) - current.value) * blend;
				current.weight += ((active ? 1 : 0) - current.weight) * blend;
			}
			if (!active && current.weight < .001) {
				this.pose.delete(name);
				continue;
			}
			this.pose.set(name, current);
			model.setParameterValueByIndex(index, current.value, current.weight);
		}
	}
	release() {
		for (const { motion, entry } of this.clips.values()) {
			motion.release();
			entry.release();
		}
		this.clips.clear();
	}
};
// examples/packages/live2d_pet/src/action-pose.js
function validateCapabilities(catalog, owner) {
	const ids = owner.getModel().getModel().parameters.ids;
	for (const [id, { playback: p }] of Object.entries(catalog.actions)) if (!(p.type === "preset" || p.type === "pose" && Object.keys(p.parameters).every((name) => ids.includes(name) && !owner.appearanceParameters.has(ids.indexOf(name))) || p.type === "expression" && owner.expressionDefinitions.has(p.name) && !owner.adapter.appearanceStates?.includes(p.name) || p.type === "motion" && owner.workActions.clips.has(p.name))) throw new Error(`模型不支持动作 ${id} 的参数、表情或动画。`);
}
var ActionPose = class {
	constructor(owner) {
		this.owner = owner;
		this.values = /* @__PURE__ */ new Map();
		this.transitioning = false;
	}
	apply(model, dt, action, sleeping) {
		if (sleeping) {
			this.values.clear();
			this.transitioning = false;
			return;
		}
		const p = action?.playback;
		this.transitioning = p?.type === "motion" && (action.elapsed || 0) < (this.owner.workActions.clips.get(p.name)?.duration || 0);
		let target = {};
		if (p?.type === "pose") target = p.parameters;
		if (p?.type === "motion") target = this.owner.workActions.sampleClip(model, p.name, action.elapsed || 0);
		const ids = model.getModel().parameters.ids;
		if (p?.type === "expression") for (const parameter of this.owner.expressionDefinitions.get(p.name)?.Parameters || []) {
			const index = ids.indexOf(parameter.Id);
			if (index < 0) continue;
			const base = target[parameter.Id] ?? model.getParameterValueByIndex(index);
			target[parameter.Id] = parameter.Blend === "Multiply" ? base * parameter.Value : parameter.Blend === "Overwrite" ? parameter.Value : base + parameter.Value;
		}
		const blend = 1 - Math.exp(-dt / .12);
		for (const name of /* @__PURE__ */ new Set([...this.values.keys(), ...Object.keys(target)])) {
			const index = ids.indexOf(name);
			if (index < 0) continue;
			const active = Object.hasOwn(target, name);
			const pose = this.values.get(name) || {
				value: model.getParameterValueByIndex(index),
				weight: 0
			};
			if (Math.abs((active ? 1 : 0) - pose.weight) > .001 || active && Math.abs(target[name] - pose.value) > .01) this.transitioning = true;
			if (active) pose.value += (target[name] - pose.value) * blend;
			pose.weight += ((active ? 1 : 0) - pose.weight) * blend;
			if (!active && pose.weight < .001) {
				this.values.delete(name);
				continue;
			}
			this.values.set(name, pose);
			const targets = this.owner.adapter.aliases[name] ? [name, this.owner.adapter.aliases[name]] : [name];
			for (const id of targets) {
				const i = ids.indexOf(id);
				if (i >= 0) model.setParameterValueByIndex(i, Math.max(model.getParameterMinimumValue(i), Math.min(model.getParameterMaximumValue(i), pose.value)), pose.weight);
			}
		}
	}
};
// examples/packages/live2d_pet/src/gesture-motions.js
function loadGestureMotions(owner) {
	const times = [
		0,
		.16,
		.48,
		.82,
		1.12,
		1.55
	];
	const channels = owner.adapter.nod;
	const Curves = Object.entries(channels).map(([Id, values]) => {
		const Segments = [0, values[0]];
		for (let i = 1; i < times.length; i++) {
			const start = times[i - 1];
			const span = times[i] - start;
			Segments.push(1, start + span / 3, values[i - 1], start + 2 * span / 3, values[i], times[i], values[i]);
		}
		return {
			Target: "Parameter",
			Id,
			Segments
		};
	});
	const data = new TextEncoder().encode(JSON.stringify({
		Version: 3,
		Meta: {
			Duration: times.at(-1),
			Fps: 30,
			Loop: false,
			AreBeziersRestricted: true,
			CurveCount: Curves.length,
			TotalSegmentCount: Curves.length * (times.length - 1),
			TotalPointCount: Curves.length * (1 + 3 * (times.length - 1)),
			UserDataCount: 0,
			TotalUserDataSize: 0
		},
		Curves
	})).buffer;
	owner.workActions.load(owner, "Nod", data);
}
// examples/packages/live2d_pet/src/touch.js
function inTriangle(x, y, vertices, a, b, c) {
	const side = (i, j) => (x - vertices[j * 2]) * (vertices[i * 2 + 1] - vertices[j * 2 + 1]) - (vertices[i * 2] - vertices[j * 2]) * (y - vertices[j * 2 + 1]);
	const s1 = side(a, b), s2 = side(b, c), s3 = side(c, a);
	if (Math.abs((vertices[b * 2] - vertices[a * 2]) * (vertices[c * 2 + 1] - vertices[a * 2 + 1]) - (vertices[c * 2] - vertices[a * 2]) * (vertices[b * 2 + 1] - vertices[a * 2 + 1])) < 1e-10) return false;
	return !((s1 < 0 || s2 < 0 || s3 < 0) && (s1 > 0 || s2 > 0 || s3 > 0));
}
function hitRegion(player, clientX, clientY) {
	const rect = player.canvas.getBoundingClientRect();
	const b = player.bounds, aspect = player.canvas.width / player.canvas.height;
	const scale = Math.min(1.8 / (b.top - b.bottom), 1.8 * aspect / (b.right - b.left)) * player.zoom;
	const x = ((clientX - rect.left) / rect.width * 2 - 1 - player.pan.x) * aspect / scale + (b.left + b.right) / 2;
	const y = (1 - (clientY - rect.top) / rect.height * 2 - player.pan.y) / scale + (b.top + b.bottom) / 2;
	const model = player.model.getModel(), core = model.getModel();
	const heads = new Set(modelAdapter(player.model.profile).headParts);
	let body = false;
	for (let i = 0; i < model.getDrawableCount(); i++) {
		if (model.getDrawableOpacity(i) < .05) continue;
		const vertices = model.getDrawableVertices(i), indices = model.getDrawableVertexIndices(i);
		let hit = false;
		for (let j = 0; j < indices.length && !hit; j += 3) hit = inTriangle(x, y, vertices, indices[j], indices[j + 1], indices[j + 2]);
		if (!hit) continue;
		let part = core.drawables.parentPartIndices[i];
		while (part >= 0) {
			if (heads.has(core.parts.ids[part])) return "head";
			part = core.parts.parentIndices[part];
		}
		body = true;
	}
	return body ? "body" : null;
}
function applyReaction(model, reaction, protectedParameters = /* @__PURE__ */ new Set(), aliases = {}) {
	const ids = model.getModel().parameters.ids;
	for (const [name, amount] of Object.entries(reaction || {})) {
		const target = aliases[name] || name;
		const index = ids.indexOf(target);
		if (index < 0 || protectedParameters.has(name) || protectedParameters.has(target)) continue;
		model.setParameterValueByIndex(index, Math.max(model.getParameterMinimumValue(index), Math.min(model.getParameterMaximumValue(index), model.getParameterValueByIndex(index) + amount)));
	}
}
// examples/packages/live2d_pet/ui/speech.js
var SpeechMouth = class {
	constructor(now = () => performance.now(), wallClock = () => Date.now()) {
		this.now = now;
		this.wallClock = wallClock;
		this.enabled = true;
		this.suspended = false;
		this.key = void 0;
		this.sequence = 0;
		this.serial = 0;
		this.reset();
	}
	reset() {
		this.queue = [];
		this.current = null;
		this.value = 0;
		this.lastSample = this.now();
		this.lastText = -Infinity;
		this.speaking = false;
	}
	configure(enabled) {
		if (this.enabled === enabled) return;
		this.enabled = enabled;
		this.reset();
	}
	suspend(value) {
		if (this.suspended === value) return;
		this.suspended = value;
		this.reset();
	}
	observe(work, onText) {
		const speech = work?.speech;
		const key = work?.connected ? JSON.stringify([
			work.hostId,
			work.sessionId,
			work.creatureId,
			speech?.stream
		]) : null;
		if (key !== this.key) {
			this.key = key;
			this.sequence = speech?.sequence || 0;
			this.reset();
			return;
		}
		if (!key || !speech || speech.sequence <= this.sequence) return;
		this.sequence = speech.sequence;
		if (!speech.active) {
			this.queue = [];
			this.current = null;
			this.speaking = false;
			return;
		}
		if (!this.enabled || this.wallClock() - speech.at > 1200) return;
		onText?.();
		if (this.suspended) return;
		const text = typeof speech.text === "string" ? speech.text.slice(-256) : "";
		const beats = [];
		for (const token of text.match(/[a-zA-Z]+|[\p{L}\p{N}]|[，,、；;：:。.!！?？…\n]/gu) || []) {
			const pause = /[。.!！?？…\n]/u.test(token) ? 300 : /[，,、；;：:]/u.test(token) ? 170 : 0;
			const count = pause ? 1 : /^[a-zA-Z]+$/.test(token) ? Math.min(4, Math.ceil(token.length / 3)) : 1;
			for (let i = 0; i < count; i++) {
				const variation = ++this.serial * 7 % 11;
				beats.push({
					duration: pause || 125 + variation * 7,
					peak: pause ? 0 : .42 + variation * .027
				});
			}
		}
		if (!beats.length) return;
		this.lastText = this.now();
		this.speaking = true;
		this.queue.push(...beats);
		let duration = this.queue.reduce((total, beat) => total + beat.duration, 0);
		while (duration > 900 && this.queue.length > 1) duration -= this.queue.shift().duration;
	}
	sample() {
		const now = this.now();
		const elapsed = Math.max(0, Math.min(100, now - this.lastSample));
		this.lastSample = now;
		if (!this.enabled || this.suspended) return null;
		if (now - this.lastText > 1e3) {
			this.current = null;
			this.queue = [];
			this.speaking = false;
		}
		if (this.current && now >= this.current.end) this.current = null;
		if (!this.current && this.queue.length) {
			const beat = this.queue.shift();
			this.current = {
				...beat,
				start: now,
				end: now + beat.duration
			};
		}
		const beat = this.current;
		const phase = beat ? Math.min(1, (now - beat.start) / beat.duration) : 0;
		const target = beat ? beat.peak * Math.sin(Math.PI * phase) ** 1.4 : 0;
		this.value += (target - this.value) * (1 - Math.exp(-elapsed / (target > this.value ? 28 : 42)));
		if (this.value < .002 && !target) this.value = 0;
		return !this.speaking && !this.value ? null : this.value;
	}
};
// examples/packages/live2d_pet/src/player.js
function initializeFramework() {
	if (!CubismFramework.isStarted()) {
		CubismFramework.startUp();
		CubismFramework.initialize();
	}
}
var DragonModel = class extends CubismUserModel {
	constructor(profile = "dragon") {
		super();
		this.profile = profile;
		this.adapter = modelAdapter(profile);
		this.workActions = new WorkActions(profile);
		this.actionPose = new ActionPose(this);
		this.motions = /* @__PURE__ */ new Map();
		this.expressions = /* @__PURE__ */ new Map();
		this.expressionDefinitions = /* @__PURE__ */ new Map();
		this.appearanceParameters = /* @__PURE__ */ new Set();
		this.activeExpressions = /* @__PURE__ */ new Set();
		this.currentMotion = "Idle";
	}
	createRenderer(width, height) {
		const groups = new Set(this._model.getDrawableMasks().filter((mask) => mask.length > 0).map((mask) => [...mask].sort((a, b) => a - b).join(",")));
		const buffers = groups.size <= 36 ? 1 : Math.ceil(groups.size / 32);
		super.createRenderer(width, height, buffers);
		if (buffers > 1) this.getRenderer().setClippingMaskBufferSize(1024);
	}
	validateActionSet(catalog) {
		validateCapabilities(catalog, this);
	}
	loadMotion(...args) {
		const motion = super.loadMotion(...args);
		motion?.setEffectIds(["ParamEyeLOpen", "ParamEyeROpen"].map((name) => CubismFramework.getIdManager().getId(name)), []);
		return motion;
	}
	initializeEffects(setting) {
		loadGestureMotions(this);
		const id = (name) => CubismFramework.getIdManager().getId(name);
		this._eyeBlink = CubismEyeBlink.create(setting);
		if (!setting) this._eyeBlink.setParameterIds([id("ParamEyeLOpen"), id("ParamEyeROpen")]);
		this._breath = CubismBreath.create();
		this._breath.setParameters([new BreathParameterData(id("ParamBreath"), .5, .5, 3.2, 1)]);
	}
	loadExpression(buffer, size, name) {
		const definition = JSON.parse(new TextDecoder().decode(buffer));
		this.expressionDefinitions.set(name, definition);
		if (this.adapter.appearanceStates?.includes(name)) {
			const ids = this._model.getModel().parameters.ids;
			for (const { Id } of definition.Parameters) {
				const index = ids.indexOf(Id);
				if (index >= 0) this.appearanceParameters.add(index);
			}
		}
		return super.loadExpression(buffer, size, name);
	}
	motion(name) {
		const motion = this.motions.get(name);
		if (!motion) throw new Error(`Unknown motion: ${name}`);
		this._motionManager.stopAllMotions();
		this._model.loadParameters();
		for (let i = 0; i < this._model.getParameterCount(); i++) this._model.setParameterValueByIndex(i, this._model.getParameterDefaultValue(i));
		this._model.saveParameters();
		this.currentMotion = name;
		this._motionManager.startMotionPriority(motion, false, 3);
	}
	expression(name) {
		this.workActions.refreshBounds();
		if (!name) {
			this._expressionManager.stopAllMotions();
			this.activeExpressions.clear();
			return [];
		}
		if (!this.expressionDefinitions.has(name)) throw new Error(`Unknown expression: ${name}`);
		const active = new Set(toggleExpression(this.adapter, this.activeExpressions, name));
		this.activeExpressions = active;
		const parameters = [...active].flatMap((key) => this.expressionDefinitions.get(key).Parameters);
		const buffer = new TextEncoder().encode(JSON.stringify({
			Type: "Live2D Expression",
			FadeInTime: .4,
			FadeOutTime: .4,
			Parameters: parameters
		})).buffer;
		this._expressionManager.startMotion(super.loadExpression(buffer, buffer.byteLength, "combined"), true);
		return [...active];
	}
	update(dt, gaze, physics, reaction, action, mouth = null) {
		const model = this._model;
		model.loadParameters();
		this._motionManager.updateMotion(model, dt);
		for (const index of this.appearanceParameters) model.setParameterValueByIndex(index, model.getParameterDefaultValue(index));
		model.saveParameters();
		this._expressionManager.updateMotion(model, dt);
		const appearance = [...this.appearanceParameters].map((index) => [index, model.getParameterValueByIndex(index)]);
		if (this.currentMotion !== "Sleep") {
			this._eyeBlink.updateParameters(model, dt);
			this._dragManager.set(gaze.x, gaze.y);
			this._dragManager.update(dt);
			const x = this._dragManager.getX();
			const y = this._dragManager.getY();
			for (const [name, value] of [
				["ParamAngleX", x * 20],
				["ParamAngleY", y * 15],
				["ParamBodyAngleX", x * 6],
				["ParamEyeBallX", x],
				["ParamEyeBallY", y]
			]) model.addParameterValueById(CubismFramework.getIdManager().getId(name), value);
		}
		this._breath.updateParameters(model, dt);
		this.workActions.apply(model, dt, action, this.currentMotion === "Sleep");
		if (physics) this._physics?.evaluate(model, dt);
		else if (this.currentMotion !== "Sleep") for (const [name, alias] of Object.entries(this.adapter.aliases)) {
			const id = CubismFramework.getIdManager();
			model.setParameterValueById(id.getId(alias), model.getParameterValueById(id.getId(name)));
		}
		if (this.currentMotion !== "Sleep") applyReaction(model, reaction, new Set([...this.activeExpressions].flatMap((name) => this.expressionDefinitions.get(name).Parameters.map((parameter) => parameter.Id))), this.adapter.aliases);
		this.actionPose.apply(model, dt, action, this.currentMotion === "Sleep");
		this.applySpeech(this.currentMotion === "Sleep" ? null : mouth);
		for (const [index, value] of appearance) model.setParameterValueByIndex(index, value);
		model.update();
	}
	applySpeech(value) {
		const model = this.getModel();
		const id = CubismFramework.getIdManager().getId(this.adapter.mouth);
		this.mouthBase = value == null ? null : model.getParameterValueById(id);
		if (value != null) model.setParameterValueById(id, Math.max(0, Math.min(1, value)));
	}
	clearSpeech() {
		if (this.mouthBase == null) return;
		const model = this.getModel();
		model.setParameterValueById(CubismFramework.getIdManager().getId(this.adapter.mouth), this.mouthBase);
		this.mouthBase = null;
		model.update();
	}
	release() {
		this.workActions.release();
		this._motionManager?.stopAllMotions();
		this._expressionManager?.stopAllMotions();
		for (const motion of this.motions.values()) motion.release();
		for (const expression of this.expressions.values()) expression.release();
		this.motions.clear();
		this.expressions.clear();
		this.expressionDefinitions.clear();
		this.appearanceParameters.clear();
		this.activeExpressions.clear();
		super.release();
	}
};
var DragonPlayer = class {
	hitRegion(x, y) {
		return hitRegion(this, x, y);
	}
	constructor(canvas, report) {
		this.canvas = canvas;
		this.report = report;
		this.abort = new AbortController();
		this.textures = [];
		this.gaze = {
			x: 0,
			y: 0
		};
		this.physics = true;
		this.tracking = true;
		this.paused = false;
		this.speech = new SpeechMouth();
		this.zoom = 1;
		this.pan = {
			x: 0,
			y: 0
		};
		this.frames = 0;
		this.dead = false;
		this.frameStats = new FrameStats();
		this.diagnostics = false;
		this.mouthRange = [1, 0];
	}
	async bytes(url) {
		if (this.resourceBytes) return this.resourceBytes(url, this.abort.signal);
		const response = await fetch(url, { signal: this.abort.signal });
		if (!response.ok) throw new Error(`资源读取失败 (${response.status}): ${new URL(url).pathname.split("/").pop()}`);
		return response.arrayBuffer();
	}
	async load(manifestUrl, shaderUrl) {
		initializeFramework();
		this.gl = this.canvas.getContext("webgl", {
			alpha: true,
			premultipliedAlpha: true,
			antialias: true
		});
		if (!this.gl) throw new Error("当前窗口无法创建 WebGL，请尝试在浏览器打开预览。");
		this.report("读取原始模型…");
		const buffer = await this.bytes(manifestUrl);
		const manifest = JSON.parse(new TextDecoder().decode(buffer));
		const setting = new CubismModelSettingJson(buffer, buffer.byteLength);
		this.setting = setting;
		const asset = (path) => new URL(path, manifestUrl).href;
		this.textureBytes = manifest.Demo?.textureBytes;
		this.targetFps = modelAdapter(manifest.Demo?.profile).fps;
		const model = new DragonModel(manifest.Demo?.profile);
		this.model = model;
		const moc = await this.bytes(asset(manifest.FileReferences.Moc));
		model.loadModel(moc, true);
		if (!model.getModel()) throw new Error("模型未通过 Cubism 加载或完整性检查。");
		model.initializeEffects(setting);
		if (manifest.FileReferences.Physics) {
			const physics = await this.bytes(asset(manifest.FileReferences.Physics));
			model.loadPhysics(physics, physics.byteLength);
		}
		for (const [name, motions] of Object.entries(manifest.FileReferences.Motions)) {
			const data = await this.bytes(asset(motions[0].File));
			const motion = model.loadMotion(data, data.byteLength, name, void 0, void 0, setting, name, 0);
			if (!motion) throw new Error(`无法加载动作: ${name}`);
			motion.setLoop(true);
			model.motions.set(name, motion);
			model.workActions.load(model, name, data);
		}
		for (const expression of manifest.FileReferences.Expressions) {
			const data = await this.bytes(asset(expression.File));
			const motion = model.loadExpression(data, data.byteLength, expression.Name);
			if (!motion) throw new Error(`无法加载表情: ${expression.Name}`);
			model.expressions.set(expression.Name, motion);
		}
		for (const [name, path] of Object.entries(model.adapter.workMotions)) model.workActions.load(model, name, await this.bytes(asset(path)));
		this.resize();
		model.createRenderer(this.canvas.width, this.canvas.height);
		const renderer = model.getRenderer();
		renderer.startUp(this.gl);
		renderer.setIsPremultipliedAlpha(true);
		this.shaderUrl = shaderUrl;
		this.report(`准备 ${manifest.FileReferences.Textures.length} 张纹理…`);
		for (const [index, path] of manifest.FileReferences.Textures.entries()) {
			const bytes = await this.bytes(asset(path));
			const blobUrl = URL.createObjectURL(new Blob([bytes], { type: path.toLowerCase().endsWith(".webp") ? "image/webp" : "image/png" }));
			try {
				const img = new Image();
				img.src = blobUrl;
				await img.decode();
				this.abort.signal.throwIfAborted();
				const gl = this.gl;
				if (Math.max(img.width, img.height) > gl.getParameter(gl.MAX_TEXTURE_SIZE)) throw new Error("纹理超过当前设备支持的尺寸，请选择日常档。");
				const texture = gl.createTexture();
				this.textures.push(texture);
				gl.bindTexture(gl.TEXTURE_2D, texture);
				gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
				gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
				gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
				gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
				gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
				gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
				renderer.bindTexture(index, texture);
			} finally {
				URL.revokeObjectURL(blobUrl);
			}
		}
		model.getModel().update();
		this.bounds = this.measure();
		model.motion("Idle");
		renderer.loadShaders(shaderUrl);
		this.report("准备画面…");
		this.started = performance.now();
		this.last = this.started;
		this.fpsTime = this.started;
		this.observer = new ResizeObserver(() => this.resize());
		this.observer.observe(this.canvas);
		this.raf = requestAnimationFrame((time) => this.tick(time));
	}
	measure() {
		const model = this.model.getModel();
		const bounds = {
			left: Infinity,
			right: -Infinity,
			bottom: Infinity,
			top: -Infinity
		};
		const parameter = this.model.adapter.boundsParameter;
		const wing = CubismFramework.getIdManager().getId(parameter || "");
		const index = model.getParameterIndex(wing);
		const original = model.getParameterValueById(wing);
		const values = parameter ? [
			original,
			model.getParameterMinimumValue(index),
			model.getParameterMaximumValue(index)
		] : [original];
		for (const value of values) {
			if (parameter) model.setParameterValueById(wing, value);
			model.update();
			for (let i = 0; i < model.getDrawableCount(); i++) {
				if (model.getDrawableOpacity(i) <= 0) continue;
				const vertices = model.getDrawableVertices(i);
				for (let j = 0; j < vertices.length; j += 2) {
					bounds.left = Math.min(bounds.left, vertices[j]);
					bounds.right = Math.max(bounds.right, vertices[j]);
					bounds.bottom = Math.min(bounds.bottom, vertices[j + 1]);
					bounds.top = Math.max(bounds.top, vertices[j + 1]);
				}
			}
		}
		if (parameter) model.setParameterValueById(wing, original);
		model.update();
		if (!Number.isFinite(bounds.left)) throw new Error("模型没有可显示的网格。");
		return bounds;
	}
	resize() {
		const rect = this.canvas.getBoundingClientRect();
		const ratio = Math.min(devicePixelRatio || 1, 2);
		this.canvas.width = Math.max(1, Math.round(rect.width * ratio));
		this.canvas.height = Math.max(1, Math.round(rect.height * ratio));
	}
	tick(time) {
		if (this.dead) return;
		if (this.targetFps === 30 && time - this.last < 1e3 / 30 - .5) {
			this.raf = requestAnimationFrame((next) => this.tick(next));
			return;
		}
		try {
			const dt = Math.min((time - this.last) / 1e3, .05);
			this.last = time;
			this.speech.suspend(document.hidden || this.paused || this.model.currentMotion === "Sleep");
			if (!CubismShaderManager_WebGL.getInstance().getShader(this.gl)._isShaderLoaded) {
				if (time - this.started > 2e4) throw new Error("着色器准备超时，请重新载入。");
			} else if (!document.hidden) {
				const started = this.diagnostics ? performance.now() : 0;
				if (!this.paused) this.model.update(dt, this.tracking ? this.gaze : {
					x: 0,
					y: 0
				}, this.physics, this.behavior?.sample(time), this.behavior?.action(time), this.speech.sample());
				else this.model.clearSpeech();
				this.draw();
				if (this.diagnostics) this.recordFrame(time, performance.now() - started);
				else this.frameStats.reset();
				this.frames++;
				if (time - this.fpsTime >= 1e3) {
					this.report("已就绪", {
						stats: this.diagnostics ? this.frameStats.summary() : null,
						fps: Math.round(this.frames * 1e3 / (time - this.fpsTime)),
						drawables: this.model.getModel().getDrawableCount(),
						mouthRange: this.mouthRange.map((value) => Math.round(value * 100))
					});
					this.mouthRange = [1, 0];
					this.frames = 0;
					this.fpsTime = time;
				}
			}
			this.raf = requestAnimationFrame((next) => this.tick(next));
		} catch (error) {
			this.report(error.message, { error: true });
			this.dispose();
		}
	}
	recordFrame(time, cost) {
		this.frameStats.add(time, cost);
		const mouth = this.model.getModel().getParameterValueById(CubismFramework.getIdManager().getId(this.model.adapter.mouth));
		this.mouthRange[0] = Math.min(this.mouthRange[0], mouth);
		this.mouthRange[1] = Math.max(this.mouthRange[1], mouth);
	}
	draw() {
		const gl = this.gl;
		const b = this.bounds;
		const aspect = this.canvas.width / this.canvas.height;
		const scale = Math.min(1.8 / (b.top - b.bottom), 1.8 * aspect / (b.right - b.left)) * this.zoom;
		const matrix = new CubismMatrix44();
		matrix.scale(scale / aspect, scale);
		matrix.translate(-(b.left + b.right) / 2 * scale / aspect + this.pan.x, -(b.top + b.bottom) / 2 * scale + this.pan.y);
		gl.viewport(0, 0, this.canvas.width, this.canvas.height);
		gl.clearColor(0, 0, 0, 0);
		gl.clear(gl.COLOR_BUFFER_BIT);
		const renderer = this.model.getRenderer();
		renderer.setMvpMatrix(matrix);
		renderer.setRenderState(null, [
			0,
			0,
			this.canvas.width,
			this.canvas.height
		]);
		renderer.drawModel(this.shaderUrl);
		this.afterDraw?.();
	}
	dispose() {
		if (this.dead) return;
		this.dead = true;
		this.speech.reset();
		this.abort.abort();
		cancelAnimationFrame(this.raf);
		this.observer?.disconnect();
		this.model?.release();
		this.setting?.release();
		for (const texture of this.textures) this.gl.deleteTexture(texture);
		this.textures.length = 0;
		this.gl?.getExtension("WEBGL_lose_context")?.loseContext();
	}
};
export { DragonModel, DragonPlayer, initializeFramework };
