/**
 * Copyright(c) Live2D Inc. All rights reserved.
 *
 * Use of this source code is governed by the Live2D Open Software license
 * that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
 */


varying vec2 v_texCoord; //v2f.texcoord
varying vec2 v_blendCoord;
varying vec4 v_clipPos;
uniform sampler2D s_texture0; //_MainTex
uniform sampler2D s_blendTexture;
uniform vec4 u_baseColor; //v2f.color
uniform vec4 u_multiplyColor;
uniform vec4 u_screenColor;
uniform sampler2D s_texture1; // _ClippingMaskTex
uniform float u_invertClippingMask;
uniform vec4 u_channelFlag;

vec3 ColorBlend(vec3 colorSource, vec3 colorDestination);
vec4 AlphaBlend(vec3 C, vec3 Cs, float As, vec3 Cd, float Ad);

void main()
{
  vec4 renderTextureColor = texture2D(s_blendTexture, v_blendCoord);
  vec3 colorDestination = renderTextureColor.rgb;
  float alphaDestination = renderTextureColor.a;

  if (alphaDestination < 0.00001)
  {
    colorDestination = vec3(0.0, 0.0, 0.0);
  }
  else {
    colorDestination /= alphaDestination;
  }

  vec4 texColor = texture2D(s_texture0, v_texCoord);
  texColor.rgb *= u_multiplyColor.rgb;
  texColor.rgb = (texColor.rgb + u_screenColor.rgb) - (texColor.rgb * u_screenColor.rgb);

  texColor *= u_baseColor;
  vec3 colorSource = texColor.rgb;
  float alphaSource = texColor.a;

  if (alphaSource < 0.00001)
  {
    colorSource = vec3(0.0, 0.0, 0.0);
  }
  else {
    colorSource /= alphaSource;
  }

#ifdef CLIPPING_MASK
    float maskVal = 1.0;
    vec4 clipMask = (1.0 - texture2D(s_texture1, v_clipPos.xy / v_clipPos.w)) * u_channelFlag;
    maskVal = clipMask.r + clipMask.g + clipMask.b + clipMask.a;
    maskVal = abs(u_invertClippingMask - maskVal);

    alphaSource *= maskVal;
#endif

  vec4 source = vec4(colorSource.r, colorSource.g, colorSource.b, alphaSource);
  vec4 destination = vec4(colorDestination.r, colorDestination.g, colorDestination.b, alphaDestination);

  gl_FragColor = AlphaBlend(ColorBlend(colorSource, colorDestination), source, destination);
}
